"""
统一日志配置
- 输出到终端 + 文件
- 支持自动轮转（单文件 10MB，保留 5 个）
- 支持命名空间（travel_planner.xxx）
"""
import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from app.config import settings


def setup_logger(name: str = "travel_planner") -> logging.Logger:
    logger = logging.getLogger(name)

    # 字符串级别 → logging 常量（兼容 Python 3.10-）
    level = getattr(logging, str(settings.LOG_LEVEL).upper(), logging.INFO)
    logger.setLevel(level)

    # 避免重复添加 handler
    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # Handler 1: 终端输出
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # Handler 2: 文件输出（自动轮转）
        os.makedirs(settings.LOG_DIR, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=os.path.join(settings.LOG_DIR, "app.log"),
            maxBytes=10 * 1024 * 1024,   # 10MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger


logger = setup_logger()