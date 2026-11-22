"""
FreeLake 日志配置（freelake/logging_setup.py）
==============================================

统一日志入口：把「有人发帖」「旧库归档」这类运行事件从 `print()` 改成
标准 logging，避免生产环境只有 stdout 里的一行裸文本、也无法分级过滤。

约定：
- 业务模块里一律 `logger = logging.getLogger(__name__)`，不要 print。
- 入口（main.py）调用一次 `configure_logging()`；库代码不自行 basicConfig。
- 默认 INFO 级别、输出到 stderr；需要文件日志时设置环境变量
  ``FREELAKE_LOG_FILE``（路径），会同时写文件。
"""

from __future__ import annotations

import logging
import os

_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    """配置根 logger（幂等：重复调用不会叠加 handler）。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_file = os.environ.get("FREELAKE_LOG_FILE", "").strip()
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=handlers,
        force=True,
    )
    _CONFIGURED = True
