"""
只读 SQL 执行（freelake/db/query.py）
=====================================

管理后台的「SQL 查询」工具：首词白名单 + SQLite authorizer 双层守卫，
执行结果统一脱敏。这是唯一允许执行用户手写 SQL 的入口。
"""

import sqlite3

from peewee import DatabaseError

from freelake.config import SENSITIVE_COLUMNS
from freelake.db.connection import db


def mask_sensitive_rows(rows: list[dict]) -> list[dict]:
    """把结果集中的敏感列替换为 `***`：后台「只读 SQL」展示前必须过一遍。

    查询是只读的，但 `SELECT * FROM _user` 会把密码哈希 / 密钥哈希
    直接渲染在页面上（肩窥 / 投屏 / 截图都会泄露），因此展示层脱敏。
    """
    return [
        {key: ("***" if key.lower() in SENSITIVE_COLUMNS else value) for key, value in row.items()}
        for row in rows
    ]


def format_size(size_bytes: int) -> str:
    """将字节数转换为人类可读的文件大小格式"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    else:
        return f"{size_bytes / 1024 / 1024 / 1024:.1f} GB"


# ---- 只读 SQL 守卫（管理后台「系统工具」的 SQL 查询）----
# 只校验「首个关键字」是可以绕过的：`WITH x AS (SELECT 1) DELETE FROM _post` 与
# 写型 PRAGMA（`PRAGMA user_version = 0`）都能通过首词检查，而且写入已经生效之后
# 才因为 cursor.description 为 None 抛错。这里改用 SQLite authorizer，在**编译期**
# 拒绝写动作：写语句在 prepare 阶段就失败，不可能「先毁了库再报错」。
_DENY_AUTHORIZER_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
        sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_CREATE_VTABLE,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_INDEX,
        sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER,
        sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_DROP_VTABLE,
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_REINDEX,
        sqlite3.SQLITE_ANALYZE,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_SAVEPOINT,
    }
)
# PRAGMA 只读白名单：允许带参数的（查看结构类）与只允许裸查询形式的（状态类）。
# user_version / foreign_keys / journal_mode 等**状态型** PRAGMA 只允许读，
# 不允许 `= 值`（否则一次 `PRAGMA user_version = 0` 就能让下次启动归档重建空库）。
_PRAGMA_WITH_ARG = frozenset(
    {
        "table_info",
        "table_xinfo",
        "table_list",
        "index_list",
        "index_info",
        "index_xinfo",
        "foreign_key_list",
        "integrity_check",
        "quick_check",
    }
)
_PRAGMA_BARE_ONLY = _PRAGMA_WITH_ARG | frozenset(
    {
        "database_list",
        "compile_options",
        "collation_list",
        "function_list",
        "module_list",
        "pragma_list",
        "schema_version",
        "user_version",
        "encoding",
        "page_count",
        "page_size",
    }
)


def _readonly_authorizer(action, arg1, arg2, db_name, trigger_name):
    """SQLite authorizer 回调：拒绝一切写动作与白名单外的 PRAGMA 形式。"""
    del db_name, trigger_name  # 回调签名固定，本实现用不到
    if action in _DENY_AUTHORIZER_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA:
        name = (arg1 or "").lower()
        if name not in _PRAGMA_BARE_ONLY:
            return sqlite3.SQLITE_DENY
        if arg2 is not None and name not in _PRAGMA_WITH_ARG:
            # `PRAGMA foreign_keys = OFF` / `PRAGMA user_version = 0` 这类赋值
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def execute_sql(query: str) -> list[dict[str, str]]:
    """执行只读 SQL 查询（写操作在编译期被 authorizer 拒绝）。

    管理后台的 SQL 工具只应查询：SELECT / WITH / EXPLAIN / PRAGMA。
    这里不再依赖首关键字判断，而是给连接临时挂上 authorizer，
    任何 INSERT/UPDATE/DELETE/DDL/ATTACH/写型 PRAGMA 都会在 prepare 阶段失败。
    """
    stripped = (query or "").strip().rstrip(";").strip()
    if not stripped:
        raise ValueError("SQL 不能为空")
    head = stripped.split(None, 1)[0].upper()
    if head not in ("SELECT", "WITH", "EXPLAIN", "PRAGMA"):
        raise ValueError("仅允许只读查询：SELECT / WITH / EXPLAIN / PRAGMA")
    if head == "PRAGMA" and "=" in stripped:
        raise ValueError("PRAGMA 只支持查询形式（如 PRAGMA table_info(_user)），不允许赋值")
    conn = db.connection()
    conn.set_authorizer(_readonly_authorizer)
    try:
        cursor = db.execute_sql(stripped)
        columns = [col[0] for col in cursor.description or ()]
        return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
    except DatabaseError as exc:  # authorizer 拒绝时驱动抛 DatabaseError
        raise ValueError(f"只允许只读查询（{exc}）") from exc
    finally:
        conn.set_authorizer(None)
