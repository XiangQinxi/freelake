"""数据模型 v4（表名去 v1 前缀、补全关系与审计能力）。

与 v3 的差异（详见 docs/v2-refactor-plan.md §6）：

1. 表名统一为小写复数、去下划线前缀：``users/posts/comments/...``。
2. 新增 ``sessions``：会话是独立实体，Cookie 只存随机 token（M3 接线）。
3. 新增 ``schema_meta``：结构版本与初始化时间落库，便于运维排查。
4. 新增 ``audit_logs``：管理员动作可追溯。
5. ``reports`` 用 ``target_type + target_id`` 表达多态目标，不再靠
   ``comment_id IS NULL`` 推断。
6. ``posts.author_name`` / ``comments.author_name`` 存作者名快照：
   用户被删除后内容仍可正常展示，不依赖 JOIN。
7. ``posts.updated_at``、``comments.updated_at``、``users.updated_at``
   记录编辑时间；``users.last_login_at`` 记录最近登录。
8. ``tags.usage_count`` 冗余使用次数（发布/编辑时维护），标签云无需全表聚合。
9. ``attachments.sha256`` / ``is_image``：文件完整性校验与图片筛选。
10. ``drafts`` 的标签改为 ``draft_tags`` 关联表，与文章标签保持一致。

约定：
- 时间统一 ``YYYY-MM-DD HH:MM:SS`` 字符串（字典序即时间序）。
- 作者/评论者/举报人/草稿主仍是「逻辑外键」（只存 userid 不加约束），
  删除用户时由 ``User.delete`` 显式清理关联数据；内容本身保留。
- 文章下属数据（评论/点赞/收藏/标签关联/附件）是真正的级联外键。
"""

import datetime

from peewee import (
    AutoField,
    CharField,
    Check,
    CompositeKey,
    ForeignKeyField,
    IntegerField,
    Model,
    TextField,
)
from playhouse.mysql_ext import JSONField

from const import admin, user

from .connection import db

NOW_FORMAT = "%Y-%m-%d %H:%M:%S"

# 用户状态：正常 / 停用（停用后不能登录，但内容保留）
USER_ACTIVE = "active"
USER_DISABLED = "disabled"

# 举报目标类型
TARGET_POST = "post"
TARGET_COMMENT = "comment"


def now_str() -> str:
    """当前时间的统一字符串格式（字典序即时间序）。"""
    return datetime.datetime.now().strftime(NOW_FORMAT)


class BaseModel(Model):
    class Meta:
        database = db


class UserModel(BaseModel):
    """账号表（users）。userid 是业务主键（学号 / 登录名）。"""

    userid = CharField(primary_key=True)
    username = CharField(index=True)
    password_hash = CharField(max_length=256)  # PBKDF2 哈希串，兼容旧 sha256
    role = CharField(default=user, index=True)  # const.admin / const.user
    status = CharField(default=USER_ACTIVE, index=True)  # active / disabled
    description = TextField(default="这个用户有点懒，什么也没留下~")
    avatar = CharField(default="default_avatar.jpeg")
    # 会话密钥（管理员二次确认用）：只存哈希 + 过期时间
    secret_key_hash = CharField(max_length=64, index=True, default="")
    secret_key_expires_at = CharField(max_length=19, default="")
    created_at = CharField(default=now_str)
    updated_at = CharField(default=now_str)
    last_login_at = CharField(default="")

    class Meta:
        table_name = "users"
        constraints = [
            Check(f"role IN ('{admin}', '{user}')"),
            Check(f"status IN ('{USER_ACTIVE}', '{USER_DISABLED}')"),
        ]

    def __str__(self):
        return self.userid


class SessionModel(BaseModel):
    """登录会话（sessions）。

    Cookie 里只放随机 token（客户端加密），库里存 token 的 sha256：
    这样既能单独撤销某个会话，也不必把用户密码留在浏览器里。
    ``expires_at`` / ``revoked_at`` 用同一套时间字符串。
    """

    id = AutoField()
    user_id = CharField(index=True)  # 逻辑外键 → users.userid
    token_hash = CharField(max_length=64, unique=True)
    created_at = CharField(default=now_str)
    expires_at = CharField(default="")
    revoked_at = CharField(default="")
    user_agent = CharField(default="")
    ip = CharField(default="")

    class Meta:
        table_name = "sessions"
        indexes = ((("user_id", "expires_at"), False),)


class PostModel(BaseModel):
    """文章表（posts）。"""

    id = AutoField()
    author_id = CharField(index=True)  # 逻辑外键 → users.userid
    author_name = CharField(default="")  # 作者名快照（删号后仍可展示）
    title = TextField()
    content = TextField()
    created_at = CharField(default=now_str)
    updated_at = CharField(default=now_str)
    attpassword = CharField(max_length=256, default="")  # sha256(明文 + salt)，空串=无密码
    views = IntegerField(default=0)
    like_count = IntegerField(default=0)
    bookmark_count = IntegerField(default=0)
    comment_count = IntegerField(default=0)

    class Meta:
        table_name = "posts"
        indexes = (
            (("created_at", "id"), False),
            (("author_id", "id"), False),
            (("views", "id"), False),
            (("like_count", "id"), False),
            (("bookmark_count", "id"), False),
        )


class TagModel(BaseModel):
    """标签维度表（tags）。"""

    id = AutoField()
    name = CharField(unique=True)
    usage_count = IntegerField(default=0)
    created_at = CharField(default=now_str)

    class Meta:
        table_name = "tags"


class PostTagModel(BaseModel):
    """文章-标签关联（post_tags）。主键 (post, tag) 天然去重。"""

    post = ForeignKeyField(PostModel, on_delete="CASCADE")
    tag = ForeignKeyField(TagModel, on_delete="CASCADE")
    position = IntegerField(default=0)

    class Meta:
        table_name = "post_tags"
        primary_key = CompositeKey("post", "tag")
        indexes = ((("tag", "post"), False),)


class AttachmentModel(BaseModel):
    """附件元数据（attachments）；文件本体在 attachments/ 目录。"""

    id = AutoField()
    post = ForeignKeyField(PostModel, on_delete="CASCADE", index=True)
    original_name = CharField()
    stored_name = CharField(unique=True)
    mime = CharField(default="")
    size = IntegerField(default=0)
    sha256 = CharField(max_length=64, default="")
    is_image = IntegerField(default=0)
    created_at = CharField(default=now_str)

    class Meta:
        table_name = "attachments"


class CommentModel(BaseModel):
    """评论表（comments）。post 是级联外键，删文章自动删评论。"""

    id = AutoField()
    post = ForeignKeyField(PostModel, on_delete="CASCADE", index=True)
    user_id = CharField(index=True)  # 逻辑外键 → users.userid
    author_name = CharField(default="")  # 评论者名快照
    content = TextField()
    created_at = CharField(default=now_str)
    updated_at = CharField(default=now_str)

    class Meta:
        table_name = "comments"
        indexes = ((("post", "created_at", "id"), False),)


class LikeModel(BaseModel):
    """点赞（likes）。主键 (post, user_id) 保证同一用户只能点一次。"""

    post = ForeignKeyField(PostModel, on_delete="CASCADE")
    user_id = CharField()
    created_at = CharField(default=now_str)

    class Meta:
        table_name = "likes"
        primary_key = CompositeKey("post", "user_id")
        indexes = ((("user_id",), False),)


class BookmarkModel(BaseModel):
    """收藏（bookmarks）。主键 (post, user_id)。"""

    post = ForeignKeyField(PostModel, on_delete="CASCADE")
    user_id = CharField()
    created_at = CharField(default=now_str)

    class Meta:
        table_name = "bookmarks"
        primary_key = CompositeKey("post", "user_id")
        indexes = ((("user_id", "created_at"), False),)


class ReportModel(BaseModel):
    """举报（reports）。

    多态目标：``target_type`` ∈ (post, comment) + ``target_id``；
    ``post_id`` 只是「评论所属文章」的上下文，便于后台跳转。
    被举报内容删除后记录仍保留（审计快照），因此不加外键。
    """

    id = AutoField()
    target_type = CharField(default=TARGET_POST, index=True)
    target_id = IntegerField(index=True)
    post_id = IntegerField(null=True, index=True)  # 上下文：评论所属文章 / 被举报文章
    target_user_id = CharField(default="")  # 被举报内容的作者
    reporter_id = CharField(default="", index=True)
    content_preview = TextField(default="")
    reason = TextField()
    status = CharField(default="pending", index=True)  # pending / handled / dismissed
    handled_by = CharField(default="")
    handled_at = CharField(default="")
    note = TextField(default="")
    created_at = CharField(default=now_str)

    class Meta:
        table_name = "reports"
        indexes = (
            (("status", "id"), False),
            (("target_type", "target_id"), False),
        )
        constraints = [
            Check("status IN ('pending', 'handled', 'dismissed')"),
            Check(f"target_type IN ('{TARGET_POST}', '{TARGET_COMMENT}')"),
        ]


class DraftModel(BaseModel):
    """草稿（drafts）。附件不落盘，标签存 draft_tags。"""

    id = AutoField()
    user_id = CharField(index=True)  # 逻辑外键 → users.userid
    title = TextField(default="")
    content = TextField(default="")
    attpassword = CharField(default="")  # 草稿里存明文，发布时才 sha256
    created_at = CharField(default=now_str)
    updated_at = CharField(default=now_str)

    class Meta:
        table_name = "drafts"
        indexes = ((("user_id", "id"), False),)


class DraftTagModel(BaseModel):
    """草稿-标签关联（draft_tags）。"""

    draft = ForeignKeyField(DraftModel, on_delete="CASCADE")
    tag = ForeignKeyField(TagModel, on_delete="CASCADE")
    position = IntegerField(default=0)

    class Meta:
        table_name = "draft_tags"
        primary_key = CompositeKey("draft", "tag")


class AuditLogModel(BaseModel):
    """管理员动作审计（audit_logs）。"""

    id = AutoField()
    actor_id = CharField(index=True)
    action = CharField(index=True)  # e.g. delete_post / set_role / cleanup
    target_type = CharField(default="")
    target_id = CharField(default="")
    detail = JSONField(default=dict)
    created_at = CharField(default=now_str)

    class Meta:
        table_name = "audit_logs"


class SchemaMetaModel(BaseModel):
    """结构元信息（schema_meta）：key/value 形式，便于运维排查。"""

    key = CharField(primary_key=True)
    value = CharField(default="")
    updated_at = CharField(default=now_str)

    class Meta:
        table_name = "schema_meta"


# 建表顺序即依赖顺序（被引用者在前）
ALL_MODELS = [
    UserModel,
    SessionModel,
    PostModel,
    TagModel,
    PostTagModel,
    AttachmentModel,
    CommentModel,
    LikeModel,
    BookmarkModel,
    ReportModel,
    DraftModel,
    DraftTagModel,
    AuditLogModel,
    SchemaMetaModel,
]
