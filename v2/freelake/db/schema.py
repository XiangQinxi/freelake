"""库结构初始化：建连 → 旧库归档 → 建表 → 索引 → 版本标记。

策略（与项目约定一致）：**不做数据迁移**。结构版本不一致时，把旧库改名为
``data.db.legacy-<时间戳>`` 保留，然后重建空库；这样升级永远不会因为
迁移脚本写错而丢数据。

版本记录两处：
- ``PRAGMA user_version``：SQLite 原生、零成本，用来快速判断；
- ``schema_meta`` 表：可查询、可扩展（初始化时间、归档时间等）。
"""

import datetime
import os

from .connection import db
from .models import ALL_MODELS, now_str

SCHEMA_VERSION = 4

# v1~v3 的表名（用于识别「旧库」）
_LEGACY_TABLES = ("_post", "_user")


def db_path() -> str:
    """数据库文件路径（相对当前工作目录）。"""
    return db.database


def current_schema_version() -> int:
    row = db.execute_sql("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def _has_any_table() -> bool:
    return any(db.table_exists(name) for name in _LEGACY_TABLES) or db.table_exists(
        "posts"
    )


def archive_legacy_db() -> str:
    """把旧结构库改名备份并重连，返回备份文件路径。"""
    db.close()
    path = db_path()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.legacy-{stamp}"
    if os.path.exists(path):
        os.replace(path, backup)
    db.connect(reuse_if_open=True)
    print(
        "[freelake.db] 检测到旧版数据库结构：本版本不做数据迁移，"
        f"旧库已备份为 {os.path.basename(backup)}，正在重建空库。"
    )
    return backup


def _create_partial_indexes() -> None:
    """举报去重下沉为数据库约束。

    同一举报人对同一目标的 pending 举报只能有一条；partial unique index
    只约束 pending，已处理/已驳回后可以重新举报。
    """
    db.execute_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_pending_post "
        "ON reports(reporter_id, target_id) "
        "WHERE status = 'pending' AND target_type = 'post'"
    )
    db.execute_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_pending_comment "
        "ON reports(reporter_id, target_id) "
        "WHERE status = 'pending' AND target_type = 'comment'"
    )


def _write_schema_meta() -> None:
    db.execute_sql(
        "INSERT INTO schema_meta(key, value, updated_at) VALUES('schema_version', ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at",
        (str(SCHEMA_VERSION), now_str()),
    )


def init_database() -> None:
    """建连 + 建表 + 版本校验（导入 api 时执行一次）。"""
    db.connect(reuse_if_open=True)
    if _has_any_table() and current_schema_version() != SCHEMA_VERSION:
        archive_legacy_db()
    db.create_tables(ALL_MODELS, safe=True)
    _create_partial_indexes()
    _write_schema_meta()
    if current_schema_version() != SCHEMA_VERSION:
        db.execute_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")
