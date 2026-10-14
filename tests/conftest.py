"""pytest 全局配置（tests/ 目录级）

这个文件只做两件事，目的都是让 `uv run pytest` 默认"零成本、零意外"：

1. 把项目根目录加入 sys.path，使 `import app.*` 在任意工作目录下都能成功
   （不再依赖每个测试文件顶部手写的 sys.path.append）。

2. 装一个"外网熔断器"：默认情况下，任何试图连接非本机地址的代码都会立刻抛错，
   而不是悄悄打真实 LLM 接口 / wttr.in 天气接口产生费用。
   只有显式开启集成测试后才会放行：

       $env:RUN_INTEGRATION_TESTS = "1"     # PowerShell
       uv run pytest -q -m integration
"""
import os
import socket
import sys

import pytest

# ---------------------------------------------------------------
# 1. 让 `import app.*` 可用
# ---------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ---------------------------------------------------------------
# 2. 零成本开关
# ---------------------------------------------------------------
# 是否需要跑"要联网 / 要花钱"的用例。必须由人显式开启。
RUN_INTEGRATION = os.environ.get("RUN_INTEGRATION_TESTS") == "1"

# 默认关闭 LangSmith / LangChain 追踪，避免测试把数据发到外部平台
if not RUN_INTEGRATION:
    for _var in ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING"):
        os.environ[_var] = "false"

# 允许连接的本机地址（asyncio 在 Windows 上建事件循环会连 127.0.0.1，别误伤）
_LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}

_BLOCKED_MSG = (
    "默认禁止外网访问，已拦截对 {host} 的连接。"
    "如果这个用例确实需要联网，请给它加上 @pytest.mark.integration，"
    "并用 RUN_INTEGRATION_TESTS=1 uv run pytest -m integration 运行。"
)


def _host_of(address) -> str:
    """从 socket.connect 的地址参数里取出主机名/IP。"""
    if isinstance(address, (tuple, list)) and address:
        return str(address[0])
    return str(address)


@pytest.fixture(autouse=True)
def _no_external_network(request, monkeypatch):
    """默认熔断外网连接；带 @pytest.mark.integration 的用例不受影响。"""
    if RUN_INTEGRATION or request.node.get_closest_marker("integration"):
        yield
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_connect(self, address, *args, **kwargs):
        host = _host_of(address)
        if host not in _LOOPBACK:
            raise RuntimeError(_BLOCKED_MSG.format(host=host))
        return real_connect(self, address, *args, **kwargs)

    def guarded_connect_ex(self, address, *args, **kwargs):
        host = _host_of(address)
        if host not in _LOOPBACK:
            raise RuntimeError(_BLOCKED_MSG.format(host=host))
        return real_connect_ex(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    yield
