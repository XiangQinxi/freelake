"""
FreeLake 数据层门面（api.py）
=============================

这里**只做导出与启动装配**，不再放业务实现：

- 连接与模型：`freelake/db/connection.py`、`freelake/db/models.py`、`freelake/db/schema.py`
- 行 → dict 适配：`freelake/db/adapters.py`（键名保持 v1：authorid / postid / userid）
- 只读 SQL：`freelake/db/query.py`（白名单 + SQLite authorizer + 结果脱敏）
- 业务：`freelake/services/{users,posts,content,interactions,admin,security,bootstrap}.py`
- 配置与管理员凭据：`freelake/settings.py`

导入本模块仍会 connect + 建表 + 引导管理员 + 发公告（页面依赖这个副作用，
见 AGENT.md §8.1）。**页面与 api2 只从 api 导入，不要直接 import services**，
这样以后调整服务层内部分包时页面不用改。
"""

from const import admin, announcement_tags, user  # NOQA: F401  （历史用法：页面会 from api import admin? 保留）
from freelake.config import (  # NOQA: F401
    ANNOUNCEMENT_POST_TITLE,
    ATTACHMENTS_DIR,
    ATTACHMENT_EXT_RE as _ATTACHMENT_EXT_RE,
    AVATARS_DIR,
    AVATAR_QUALITY,
    DIR,
    MAX_ATTACHMENT_BYTES,
    MAX_AVATAR_DIM,
    PBKDF2_ITERATIONS,
    PASSWORD_OK_TTL as _PASSWORD_OK_TTL,
    SECRET_KEY_TTL_DAYS,
    SENSITIVE_COLUMNS as _SENSITIVE_COLUMNS,
    VALID_USERID_RE as _VALID_USERID_RE,
    salt,
)
from freelake.db.connection import UNIQUE_VIOLATION, db  # NOQA: F401
from freelake.db.models import (  # NOQA: F401
    ALL_MODELS,
    NOW_FORMAT,
    TARGET_COMMENT,
    TARGET_POST,
    USER_ACTIVE,
    USER_DISABLED,
    AttachmentModel as _Attachment,
    BookmarkModel as _Bookmark,
    CommentModel as _Comment,
    DraftModel as _Draft,
    DraftTagModel as _DraftTag,
    LikeModel as _Like,
    PostModel as _Post,
    PostTagModel as _PostTag,
    ReportModel as _Report,
    SchemaMetaModel as _SchemaMeta,
    SessionModel as _Session,
    TagModel as _Tag,
    UserModel as _User,
    now_str,
)
from freelake.db.query import (  # NOQA: F401
    _readonly_authorizer,
    execute_sql,
    format_size,
    mask_sensitive_rows,
)
from freelake.db.schema import (  # NOQA: F401
    SCHEMA_VERSION,
    archive_legacy_db,
    current_schema_version,
    init_database,
)
from freelake.settings import Config, admin_credentials  # NOQA: F401
from freelake.services.admin import (  # NOQA: F401
    delete_comment_by_id,
    delete_orphaned_attachments,
    delete_orphaned_comments,
    export_comments_csv,
    export_comments_json,
    export_posts_csv,
    export_posts_json,
    export_users_csv,
    export_users_json,
    get_all_attachments,
    get_attachment_stats,
    get_comment_summary,
    get_orphaned_attachments,
    get_orphaned_comments,
    search_comments,
)
from freelake.services.bootstrap import bootstrap_announcements  # NOQA: F401
from freelake.services.content import (  # NOQA: F401
    Attachment,
    Draft,
    _read_attachment_file,
    _sync_draft_tags,
    build_attachments_zip,
)
from freelake.services.interactions import Bookmark, Like, Report  # NOQA: F401
from freelake.services.posts import (  # NOQA: F401
    Post,
    _comment_id_at,
    _sync_attachments,
    _sync_tags,
)
from freelake.services.security import (  # NOQA: F401
    _password_cache_key,
    _password_cached,
    _password_remember,
    _verify_password_uncached,
    generate_sk_key,
    get_avatar_bytes,
    hash_password,
    is_valid_avatar_name,
    is_valid_password,
    save_avatar,
    sha256,
    verify_password,
)
from freelake.services.users import User  # NOQA: F401
from freelake.db.adapters import (  # NOQA: F401
    _attachment_dict,
    _attachment_map,
    _comment_dict,
    _comments_map,
    _draft_dict,
    _draft_tag_map,
    _last_comment_map,
    _post_dicts,
    _report_dict,
    _tag_map,
    _user_dict,
)


# region 启动装配：导入即建库（页面依赖这个副作用）
def _db_path() -> str:
    """数据库文件路径（相对当前工作目录）。"""
    return db.database


def _current_user_version() -> int:
    return current_schema_version()


def _archive_legacy_db() -> str:
    return archive_legacy_db()


init_database()

# 启动引导：创建管理员账号（凭据来自 secrets / 环境变量 / config.toml）
_admin = admin_credentials()
if _admin.get("userid") and _admin.get("password"):
    if _User.get_or_none(_User.userid == _admin["userid"]) is None:
        _User.create(
            userid=_admin["userid"],
            username=_admin.get("username") or _admin["userid"],
            password_hash=hash_password(_admin["password"]),
            role=admin,
            created_at=now_str(),
            updated_at=now_str(),
        )

bootstrap_announcements()
# endregion
