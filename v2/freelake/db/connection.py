"""SQLite 连接定义与通用异常集合。

这里的 pragma 选择有明确理由，改动前请先读注释：

- ``foreign_keys = 1``：文章删除时由 SQLite 级联清理评论 / 点赞 / 收藏 /
  标签关联 / 附件；关掉就会出现孤儿行。
- ``busy_timeout``：并发写时排队等待，而不是立刻 ``database is locked``。
- **刻意不开 WAL**：管理后台「下载 data.db 备份」直接读取库文件字节，
  WAL 模式下最近的提交还在 ``-wal`` 文件里，备份会丢数据。
"""

import sqlite3

from peewee import IntegrityError, SqliteDatabase

DB_FILENAME = "data.db"

db = SqliteDatabase(
    DB_FILENAME,
    pragmas={"foreign_keys": 1, "busy_timeout": 5000},
)

# peewee 把驱动异常包装成自己的 IntegrityError，它**不是**
# sqlite3.IntegrityError 的子类，因此捕获时必须同时列两种类型。
UNIQUE_VIOLATION = (IntegrityError, sqlite3.IntegrityError)
