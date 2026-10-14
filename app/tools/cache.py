"""外部 API 结果的本地 TTL 缓存（SQLite）。

为什么需要：
1. 高德 / Tavily 都有配额与限流，同一次规划里天气、景点、路线可能被重复查询；
2. 演示与评测时反复跑同一批城市，缓存能显著降低延迟与外部依赖抖动；
3. 缓存命中率本身是可观测指标（/metrics 会暴露）。

实现：与业务库共用同一个 SQLite 文件，只多一张 tool_cache 表，避免引入 Redis 依赖。
"""
import hashlib
import json
import sqlite3
import threading
import time
from typing import Any, Dict, Optional

from app.config import settings
from app.utils.logger import setup_logger

logger = setup_logger("travel_planner.cache")

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_stats = {"hit": 0, "miss": 0, "set": 0}


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(settings.DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_cache (
                cache_key  TEXT PRIMARY KEY,
                namespace  TEXT NOT NULL,
                payload    TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        _conn.commit()
    return _conn


def make_key(namespace: str, **params: Any) -> str:
    """稳定的缓存键：参数排序后哈希，避免字典顺序影响命中率。"""
    raw = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{namespace}:{digest}"


def get(key: str) -> Optional[Any]:
    """读缓存；过期视为未命中并顺手清理。"""
    try:
        with _lock:
            row = _connect().execute(
                "SELECT payload, expires_at FROM tool_cache WHERE cache_key = ?", (key,)
            ).fetchone()
        if not row:
            _stats["miss"] += 1
            return None
        payload, expires_at = row
        if expires_at < time.time():
            with _lock:
                _connect().execute("DELETE FROM tool_cache WHERE cache_key = ?", (key,))
                _connect().commit()
            _stats["miss"] += 1
            return None
        _stats["hit"] += 1
        return json.loads(payload)
    except Exception as exc:  # 缓存故障绝不能影响主流程
        logger.warning(f"⚠️ 缓存读取失败，按未命中处理：{exc}")
        _stats["miss"] += 1
        return None


def set(key: str, value: Any, ttl: int, namespace: str = "default") -> None:
    """写缓存。ttl 单位秒；ttl<=0 表示不缓存。"""
    if ttl <= 0:
        return
    try:
        now = time.time()
        with _lock:
            conn = _connect()
            conn.execute(
                "INSERT OR REPLACE INTO tool_cache (cache_key, namespace, payload, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, namespace, json.dumps(value, ensure_ascii=False, default=str), now, now + ttl),
            )
            conn.commit()
        _stats["set"] += 1
    except Exception as exc:
        logger.warning(f"⚠️ 缓存写入失败（忽略）：{exc}")


def cached_call(namespace: str, ttl: int, producer, **params: Any) -> Any:
    """带缓存的调用：命中直接返回，未命中执行 producer 并写入。"""
    key = make_key(namespace, **params)
    hit = get(key)
    if hit is not None:
        logger.info(f"⚡ 缓存命中 {namespace}")
        return hit
    value = producer()
    set(key, value, ttl, namespace=namespace)
    return value


def stats() -> Dict[str, Any]:
    """缓存统计，供 /metrics 暴露。"""
    total = _stats["hit"] + _stats["miss"]
    return {
        **_stats,
        "hit_rate": round(_stats["hit"] / total, 4) if total else 0.0,
    }


def clear(namespace: Optional[str] = None) -> int:
    """清理缓存（演示前想强制走真实请求时用）。"""
    with _lock:
        conn = _connect()
        if namespace:
            cur = conn.execute("DELETE FROM tool_cache WHERE namespace = ?", (namespace,))
        else:
            cur = conn.execute("DELETE FROM tool_cache")
        conn.commit()
        return cur.rowcount
