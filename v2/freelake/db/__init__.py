"""数据访问基础层：连接、模型、结构初始化。

对外只暴露：``db``、``UNIQUE_VIOLATION``、模型类、``init_database``。
业务查询放在 ``freelake.repos``（M2 迁移目标）。
"""

from .connection import UNIQUE_VIOLATION, db
from .models import (
    ALL_MODELS,
    NOW_FORMAT,
    AttachmentModel,
    AuditLogModel,
    BookmarkModel,
    CommentModel,
    DraftModel,
    DraftTagModel,
    LikeModel,
    PostModel,
    PostTagModel,
    ReportModel,
    SchemaMetaModel,
    SessionModel,
    TagModel,
    UserModel,
    now_str,
)
from .schema import SCHEMA_VERSION, current_schema_version, init_database

__all__ = [
    "ALL_MODELS",
    "NOW_FORMAT",
    "SCHEMA_VERSION",
    "AttachmentModel",
    "AuditLogModel",
    "BookmarkModel",
    "CommentModel",
    "DraftModel",
    "DraftTagModel",
    "LikeModel",
    "PostModel",
    "PostTagModel",
    "ReportModel",
    "SchemaMetaModel",
    "SessionModel",
    "TagModel",
    "UNIQUE_VIOLATION",
    "UserModel",
    "current_schema_version",
    "db",
    "init_database",
    "now_str",
]
