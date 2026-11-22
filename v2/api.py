"""
FreeLake 数据层（api.py）
========================

基于 peewee + SQLite 的论坛数据访问层，提供用户、文章、评论、点赞、
收藏、附件等全部业务数据的读写接口，以及密码哈希、数据导出、管理员
工具等能力。

设计约定：
- 数据库模型类以下划线开头（``_User``/``_Post``/...），对外暴露的业务接口
  类无下划线（``User``/``Post``/``Attachment``/``Like``/``Bookmark``）
- 时间统一使用 ``YYYY-MM-DD HH:MM:SS`` 字符串格式存入数据库
- 用户密码：新注册用户使用 PBKDF2-SHA256 加盐哈希（``hash_password``），
  旧版 sha256 密码在登录校验通过后自动升级为 PBKDF2
- 附件/头像保存在项目根目录 ``attachments/`` 与 ``avatars/`` 目录，
  数据库仅保存文件名（相对路径），便于跨机器迁移
- 导入本模块时会自动建表，并引导创建管理员账号（凭据来自 secrets/环境变量）

依赖：peewee、Pillow、toml、streamlit（仅用于 secrets 与缩略图缓存）。
"""

import base64
import datetime
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
import sqlite3
import typing
import uuid
import zipfile

import streamlit as st
import toml
from peewee import *
from PIL import Image
from playhouse.mysql_ext import JSONField  # NOQA

from const import admin, announcement_tags, user

# 数据库连接。pragma 说明：
# - foreign_keys：打开外键约束，删除文章时由 SQLite 级联清理评论/点赞/收藏/标签/附件
# - busy_timeout：并发写时等待而不是立即报 locked
# 刻意不开 WAL：管理后台的「下载 data.db 备份」直接读库文件字节，WAL 模式下会漏数据
db = SqliteDatabase(
    "data.db",
    pragmas={"foreign_keys": 1, "busy_timeout": 5000},
)
# 唯一索引 / 外键冲突：peewee 会把 sqlite3 的异常包装成自己的 IntegrityError
UNIQUE_VIOLATION = (IntegrityError, sqlite3.IntegrityError)
salt = "freelake"

# 附件存储目录（在项目根目录下创建 attachments 文件夹）
DIR = os.path.dirname(__file__)
ATTACHMENTS_DIR = os.path.join(DIR, "attachments")  # NOQA
AVATARS_DIR = os.path.join(DIR, "avatars")  # NOQA

os.makedirs(ATTACHMENTS_DIR, exist_ok=True)
os.makedirs(AVATARS_DIR, exist_ok=True)

# 头像压缩参数：边长上限与 JPEG 质量（上传的原始高清图会被缩放重编码）
MAX_AVATAR_DIM = 512
AVATAR_QUALITY = 85
# 单个附件大小上限（与 .streamlit/config.toml 的 server.maxUploadSize 一致）
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
# 落盘扩展名只允许字母数字，杜绝 .php/.html 之类可执行/可渲染后缀
_ATTACHMENT_EXT_RE = re.compile(r"^\.[A-Za-z0-9]{1,10}$")


_SENSITIVE_COLUMNS = frozenset(
    {"password", "secret_key", "secret_key_hash", "secret_key_expires_at", "attpassword"}
)


def mask_sensitive_rows(rows: list[dict]) -> list[dict]:
    """把结果集中的敏感列替换为 `***`：后台「只读 SQL」展示前必须过一遍。

    查询是只读的，但 `SELECT * FROM _user` 会把密码哈希 / 密钥哈希
    直接渲染在页面上（肩窥 / 投屏 / 截图都会泄露），因此展示层脱敏。
    """
    return [
        {
            key: ("***" if key.lower() in _SENSITIVE_COLUMNS else value)
            for key, value in row.items()
        }
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
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    except DatabaseError as exc:  # authorizer 拒绝时驱动抛 DatabaseError
        raise ValueError(f"只允许只读查询（{exc}）") from exc
    finally:
        conn.set_authorizer(None)


def generate_sk_key(byte_length=40):
    """
    生成以 'sk' 开头的安全随机密钥。
    :param byte_length: 随机部分的字节数（结果长度约为 byte_length * 2 的十六进制形式）
    :return: 形如 'sk-' + 十六进制字符串的密钥
    """
    random_bytes = secrets.token_bytes(byte_length)
    hex_part = random_bytes.hex()  # 每个字节转成两个十六进制字符
    return "sk-" + hex_part


# region 数据库模型
# ---------------------------------------------------------------------------
# v2 模型设计（重构目标：更易读、更高性能；不做数据迁移，旧库自动备份后重建）
# ---------------------------------------------------------------------------
# 1. 表名维持 v1（_user/_post/...）：管理后台的只读 SQL 工具与历史文档都按此书写，
#    改名收益小、破坏面大。
# 2. 列名统一 snake_case；对外返回的 dict 键保持 v1 写法（authorid/postid/...），
#    由 _user_dict / _post_dicts / _comment_dict 等适配函数转换 —— 页面脚本零改动。
# 3. 时间仍是 "YYYY-MM-DD HH:MM:SS" 字符串（字典序即时间序），不迁移成 datetime。
# 4. 「逻辑外键」= 只存 id 不加约束：作者 / 评论者 / 举报人。用户被删除后内容保留，
#    页面显示「用户已注销」，与 v1 行为一致。
#    「从属外键」= ForeignKeyField(on_delete="CASCADE")：文章的评论 / 点赞 / 收藏 /
#    标签 / 附件。删除文章时由 SQLite 自动级联清理（v1 手工删评论，点赞收藏会留孤儿）。
# 5. 标签从 _post.tags(JSON 数组) 改成 _tag + _post_tag 关联表：可建索引、可统计、
#    可精确匹配（v1 靠 JSON.contains，无法走索引）。
# 6. 点赞 / 收藏 / 评论数冗余在 _post 上（like_count / bookmark_count / comment_count）：
#    列表页按热度排序直接 ORDER BY 带索引的列，不必全表 group by 再 Python 排序。
#    写路径全部集中在本模块，计数器与明细表在同一事务内增减。
# 7. 高频过滤 / 排序列都有索引，见各模型 Meta.indexes。
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 3
NOW_FORMAT = "%Y-%m-%d %H:%M:%S"
# 会话密钥（Cookie 里的 secretkey）有效期：过期后管理员操作需重新登录
SECRET_KEY_TTL_DAYS = 30


def now_str() -> str:
    """当前时间的统一字符串格式（字典序即时间序）。"""
    return datetime.datetime.now().strftime(NOW_FORMAT)


class BaseModel(Model):
    class Meta:
        database = db


class _User(BaseModel):
    """用户表（_user）。userid 是业务主键（学号 / 登录名），全站都用它引用用户。"""

    userid = CharField(primary_key=True)
    username = CharField(index=True)
    password = CharField(max_length=256)  # PBKDF2 哈希串，兼容旧 sha256
    created_at = CharField(default=now_str)
    description = TextField(default="这个用户有点懒，什么也没留下~")
    avatar = CharField(default="default_avatar.jpeg")  # 头像文件地址（文件名）
    role = CharField(default=user, index=True)  # const.admin / const.user
    # 会话密钥不再是长期明文凭据：库里只存 sha256 哈希 + 过期时间，
    # 明文只在登录成功那一刻写进加密 Cookie（见 User.issue_secret_key）。
    secret_key_hash = CharField(max_length=64, index=True, default="")
    secret_key_expires_at = CharField(max_length=19, default="")

    class Meta:
        table_name = "_user"
        constraints = [Check(f"role IN ('{admin}', '{user}')")]

    def __str__(self):
        return self.userid


class _Post(BaseModel):
    """文章表（_post）。

    附件、标签、评论都不再以 JSON 数组存在本表：
    附件 → _attachment（一对一的多行），标签 → _post_tag，评论 → _comment.post_id。
    """

    id = AutoField()
    author_id = CharField(index=True)  # 逻辑外键 → _user.userid
    title = TextField()
    content = TextField()
    created_at = CharField(default=now_str)
    attpassword = CharField(max_length=256, default="")  # sha256(明文 + salt)，空串=无密码
    views = IntegerField(default=0)
    # 冗余计数器：由 Like / Bookmark / Post.add_comment 在同一事务内维护
    like_count = IntegerField(default=0)
    bookmark_count = IntegerField(default=0)
    comment_count = IntegerField(default=0)

    class Meta:
        table_name = "_post"
        indexes = (
            (("created_at", "id"), False),  # 默认排序（时间倒序）
            (("author_id", "id"), False),  # 个人主页文章列表
            (("views", "id"), False),  # 按浏览量排序
            (("like_count", "id"), False),  # 按点赞量排序
            (("bookmark_count", "id"), False),  # 按收藏量排序
        )


class _Tag(BaseModel):
    """标签维度表（_tag）。name 唯一，用户只能选 const.tags 里的标签，但公告等
    内部内容允许使用额外标签（如「迎新」「社区规范」）。"""

    id = AutoField()
    name = CharField(unique=True)

    class Meta:
        table_name = "_tag"


class _PostTag(BaseModel):
    """文章-标签关联表（_post_tag）。主键 (post_id, tag_id) 天然去重；
    position 保留用户在发布页选择标签的先后顺序。"""

    post = ForeignKeyField(_Post, on_delete="CASCADE")
    tag = ForeignKeyField(_Tag, on_delete="CASCADE")
    position = IntegerField(default=0)

    class Meta:
        table_name = "_post_tag"
        primary_key = CompositeKey("post", "tag")
        indexes = ((("tag", "post"), False),)  # 反查：某标签下的文章


class _Attachment(BaseModel):
    """附件表（_attachment）。文件本体在 attachments/ 目录，这里只存元数据。"""

    id = AutoField()
    post = ForeignKeyField(_Post, on_delete="CASCADE", index=True)
    original_name = CharField()  # 上传时的原始文件名（下载/打包时用）
    stored_name = CharField(unique=True)  # 磁盘上的 uuid 文件名
    mime = CharField(default="")  # MIME 类型（image/png 等）
    size = IntegerField(default=0)  # 字节数
    created_at = CharField(default=now_str)

    class Meta:
        table_name = "_attachment"


class _Comment(BaseModel):
    """评论表（_comment）。post_id 为级联外键，删文章自动删评论。"""

    id = AutoField()
    post = ForeignKeyField(_Post, on_delete="CASCADE", index=True)
    user_id = CharField(index=True)  # 逻辑外键 → _user.userid
    content = TextField()
    created_at = CharField(default=now_str)

    class Meta:
        table_name = "_comment"
        indexes = ((("post", "created_at", "id"), False),)  # 文章内按时间排序


class _Like(BaseModel):
    """点赞表（_like）。主键 (post_id, user_id) 保证同一用户只能点一次。"""

    post = ForeignKeyField(_Post, on_delete="CASCADE")
    user_id = CharField()
    created_at = CharField(default=now_str)

    class Meta:
        table_name = "_like"
        primary_key = CompositeKey("post", "user_id")
        indexes = ((("user_id",), False),)


class _Bookmark(BaseModel):
    """收藏表（_bookmark）。主键 (post_id, user_id) 保证同一用户只能收藏一次。"""

    post = ForeignKeyField(_Post, on_delete="CASCADE")
    user_id = CharField()
    created_at = CharField(default=now_str)

    class Meta:
        table_name = "_bookmark"
        primary_key = CompositeKey("post", "user_id")
        indexes = (
            (("user_id", "created_at"), False),  # 「我的收藏」按收藏时间倒序
        )


class _Report(BaseModel):
    """举报表（_report）。

    post_id / comment_id 故意不加外键：举报记录是审计快照，被举报内容删除后仍需保留。
    comment_id 为 NULL 表示「举报文章」，非 NULL 表示「举报该文章下的某条评论」——
    这是区分举报目标的唯一依据。
    """

    id = AutoField()
    post_id = IntegerField(null=True, index=True)  # 被举报文章（举报评论时也记录所属文章）
    comment_id = IntegerField(null=True, index=True)  # 被举报评论；NULL = 举报文章
    target_user_id = CharField()  # 被举报内容的作者
    reporter_id = CharField()  # 举报人
    content_preview = TextField(default="")  # 被举报内容摘要快照（删除后仍可追溯）
    reason = TextField()
    status = CharField(default="pending", index=True)  # pending / handled / dismissed
    handled_by = CharField(default="")
    handled_at = CharField(default="")
    note = TextField(default="")
    created_at = CharField(default=now_str)

    class Meta:
        table_name = "_report"
        indexes = ((("status", "id"), False),)  # 后台按状态筛选 + 倒序
        constraints = [
            Check("status IN ('pending', 'handled', 'dismissed')"),
        ]


class _Draft(BaseModel):
    """用户草稿（_draft）。

    仅保留标题 / 内容 / 标签 / 专属密码；附件不落盘，需在发布前重新上传，
    避免草稿产生孤立附件文件。草稿是临时数据，标签仍用 JSON 数组（无需查询）。
    """

    id = AutoField()
    user_id = CharField(index=True)  # 逻辑外键 → _user.userid
    title = TextField(default="")
    content = TextField(default="")
    tags = JSONField(default=list)
    attpassword = CharField(default="")  # 草稿里存明文，发布时才 sha256
    created_at = CharField(default=now_str)
    updated_at = CharField(default=now_str)

    class Meta:
        table_name = "_draft"
        indexes = ((("user_id", "id"), False),)


# 建表清单（顺序即依赖顺序：被引用者在前）
ALL_MODELS = [
    _User,
    _Post,
    _Tag,
    _PostTag,
    _Attachment,
    _Comment,
    _Like,
    _Bookmark,
    _Report,
    _Draft,
]



# endregion


# region 行 → 对外 dict（v1 键名适配层）
# 页面脚本依赖的是「键名契约」而不是物理列名，因此所有对外函数统一走这里转换，
# 重构表结构时页面零改动。键名请勿随意修改（home.py / admin.py / tests 都按此读）。


def _user_dict(row) -> dict:
    """_user 行 → v1 形状 dict（键名与旧库一致）。"""
    return {
        "userid": row["userid"],
        "username": row["username"],
        "password": row["password"],
        "created_at": row["created_at"],
        "description": row["description"],
        "avatar": row["avatar"],
        "role": row["role"],
    }


def _comment_dict(row) -> dict:
    """_comment 行 → v1 形状 dict。"""
    return {
        "id": row["id"],
        "postid": row["post"],
        "userid": row["user_id"],
        "content": row["content"],
        "created_at": row["created_at"],
    }


def _report_dict(row) -> dict:
    """_report 行 → v1 形状 dict（admin.py 的举报卡片按这些键渲染）。"""
    return {
        "id": row["id"],
        "postid": row["post_id"],
        "commentid": row["comment_id"],
        "target_userid": row["target_user_id"],
        "reporter_userid": row["reporter_id"],
        "content_preview": row["content_preview"],
        "reason": row["reason"],
        "status": row["status"],
        "handled_by": row["handled_by"],
        "handled_at": row["handled_at"],
        "note": row["note"],
        "created_at": row["created_at"],
    }


def _draft_dict(row) -> dict:
    """_draft 行 → v1 形状 dict。"""
    return {
        "id": row["id"],
        "userid": row["user_id"],
        "title": row["title"],
        "content": row["content"],
        "tags": row["tags"],
        "attpassword": row["attpassword"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _attachment_dict(row) -> dict:
    """_attachment 行 → v1 附件 dict（home.py 读 saved_name/original_name/type/size）。"""
    return {
        "saved_name": row["stored_name"],
        "original_name": row["original_name"],
        "type": row["mime"],
        "size": row["size"],
    }


def _tag_map(post_ids) -> dict[int, list[str]]:
    """批量取文章标签：{post_id: [标签名, ...]}（一次查询，按发布时选择顺序）。"""
    if not post_ids:
        return {}
    rows = (
        _PostTag.select(_PostTag.post, _Tag.name)
        .join(_Tag, on=(_PostTag.tag == _Tag.id))
        .where(_PostTag.post.in_(list(post_ids)))
        .order_by(_PostTag.post, _PostTag.position)
        .dicts()
    )
    result: dict[int, list[str]] = {}
    for row in rows:
        result.setdefault(row["post"], []).append(row["name"])
    return result


def _attachment_map(post_ids) -> dict[int, list[dict]]:
    """批量取文章附件：{post_id: [附件 dict, ...]}（一次查询）。"""
    if not post_ids:
        return {}
    rows = (
        _Attachment.select()
        .where(_Attachment.post.in_(list(post_ids)))
        .order_by(_Attachment.id)
        .dicts()
    )
    result: dict[int, list[dict]] = {}
    for row in rows:
        result.setdefault(row["post"], []).append(_attachment_dict(row))
    return result


def _comments_map(post_ids) -> dict[int, list[str]]:
    """批量取文章的评论（按楼层次序），值为 json 字符串列表（v1 契约）。"""
    if not post_ids:
        return {}
    rows = (
        _Comment.select()
        .where(_Comment.post.in_(list(post_ids)))
        .order_by(_Comment.post, _Comment.created_at, _Comment.id)
        .dicts()
    )
    result: dict[int, list[str]] = {}
    for row in rows:
        result.setdefault(row["post"], []).append(
            json.dumps(_comment_dict(row), ensure_ascii=False)
        )
    return result


def _last_comment_map(post_ids) -> dict[int, dict]:
    """批量取每篇文章的最后一条评论：{post_id: 评论 dict}（首页卡片用）。"""
    if not post_ids:
        return {}
    sub = (
        _Comment.select(fn.MAX(_Comment.id).alias("cid"))
        .where(_Comment.post.in_(list(post_ids)))
        .group_by(_Comment.post)
    )
    rows = _Comment.select().where(_Comment.id.in_(sub)).dicts()
    return {row["post"]: _comment_dict(row) for row in rows}


def _post_dicts(rows, *, with_comments=False, with_last_comment=False) -> list[dict]:
    """把 _post 行列表转成 v1 形状的 dict 列表（批量补齐标签/附件/评论）。"""
    rows = list(rows)
    post_ids = [row["id"] for row in rows]
    tag_map = _tag_map(post_ids)
    attachment_map = _attachment_map(post_ids)
    comments_map = _comments_map(post_ids) if with_comments else {}
    last_map = _last_comment_map(post_ids) if with_last_comment else {}

    result = []
    for row in rows:
        pid = row["id"]
        result.append(
            {
                "id": pid,
                "authorid": row["author_id"],
                "title": row["title"],
                "content": row["content"],
                "created_at": row["created_at"],
                "attachments": attachment_map.get(pid, []),
                "attpassword": row["attpassword"],
                "tags": tag_map.get(pid, []),
                "comments": comments_map.get(pid, []),
                "views": row["views"],
                # 以下三项是 v2 新增的只读统计（v1 没有，页面可按需使用）
                "like_count": row["like_count"],
                "bookmark_count": row["bookmark_count"],
                "comment_count": row["comment_count"],
            }
        )
    if with_last_comment:
        for item in result:
            item["last_comment"] = last_map.get(item["id"])
    return result


# endregion


class Config:
    config = {}

    def __init__(self):
        self.load()

    def load(self):
        with open("config.toml", "r+", encoding="utf-8") as f:
            self.config = toml.load(f)

    def save(self):
        with open("config.toml", "w+", encoding="utf-8") as f:
            toml.dump(self.config, f)


def admin_credentials() -> dict:
    """按优先级获取管理员凭据：st.secrets > 环境变量 > config.toml。

    密码不应提交到仓库，本地开发请写入 .streamlit/secrets.toml（已 gitignore）。
    """
    # 1. st.secrets（本地 .streamlit/secrets.toml 或 Streamlit Cloud 后台）
    try:
        for section in ("admin", "default_admin"):
            item = st.secrets.get(section, {}) or {}
            if item.get("password"):
                return {
                    "userid": item.get("userid") or "admin",
                    "username": item.get("username") or item.get("userid") or "Admin",
                    "password": item["password"],
                }
    except Exception:
        pass
    # 2. 环境变量
    env = {
        "userid": os.environ.get("ADMIN_USERID"),
        "username": os.environ.get("ADMIN_USERNAME"),
        "password": os.environ.get("ADMIN_PASSWORD"),
    }
    if env.get("password"):
        env["username"] = env["username"] or env["userid"] or "Admin"
        return env
    # 3. config.toml（仅作本地兜底，不应包含真实密码）
    return Config().config.get("admin", {}) or {}


def sha256(value):
    """获取哈希加密加盐后的文本（旧版用户密码与附件密码使用）"""
    return hashlib.sha256((value + salt).encode()).hexdigest()


PBKDF2_ITERATIONS = 200_000


# 用户 ID：3–20 位字母/数字/下划线/连字符（全站用它拼 URL 与 Cookie）
_VALID_USERID_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")


def is_valid_password(password: str | None) -> bool:
    """密码规则：至少 8 位且同时包含字母与数字（与注册页提示语一致）。"""
    if not password or len(password) < 8 or len(password) > 128:
        return False
    return any(ch.isalpha() for ch in password) and any(
        ch.isdigit() for ch in password
    )


def hash_password(password: str) -> str:
    """使用 PBKDF2-SHA256 加盐哈希用户密码，返回自包含的存储字符串。"""
    pw_salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), pw_salt.encode(), PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2${PBKDF2_ITERATIONS}${pw_salt}${digest}"


_PASSWORD_OK_TTL = 300  # 秒：同一会话内「密码校验通过」的记忆有效期


def _password_cache_key(password: str, stored: str) -> str:
    return sha256(f"{password}\x00{stored}")


def _password_cached(password: str, stored: str) -> bool:
    """查会话内缓存：命中且未过期返回 True，否则 False（只缓存成功）。

    为什么要缓存：PBKDF2 200k 次迭代约 110ms，而每次 rerun 会校验 2–4 次
    （导航、页面、「是不是管理员」），单次交互就是半秒 CPU。缓存键里带
    **库里的密码哈希**：改密后哈希变了 → 旧缓存自然失效。
    """
    try:
        cache = st.session_state.get("_pw_ok")
    except Exception:  # 非 Streamlit 运行时（脚本 / 测试）就没有缓存
        return False
    if not isinstance(cache, dict):
        return False
    expires_at = cache.get(_password_cache_key(password, stored))
    return bool(expires_at) and expires_at >= time.time()


def _password_remember(password: str, stored: str) -> None:
    try:
        cache = st.session_state.get("_pw_ok")
        if not isinstance(cache, dict):
            cache = {}
            st.session_state["_pw_ok"] = cache
        cache[_password_cache_key(password, stored)] = time.time() + _PASSWORD_OK_TTL
    except Exception:
        pass


def verify_password(password: str, stored: str) -> bool:
    """校验密码；兼容旧的 `sha256(明文+salt)` 存储格式。

    成功结果在会话内记忆 5 分钟（键含存储哈希，改密即失效）。
    """
    if _password_cached(password, stored):
        return True
    ok = _verify_password_uncached(password, stored)
    if ok:
        _password_remember(password, stored)
    return ok


def _verify_password_uncached(password: str, stored: str) -> bool:
    """真正的哈希比对（不含缓存）。"""
    if stored.startswith("pbkdf2$"):
        try:
            _, iterations, pw_salt, digest = stored.split("$", 3)
            calc = hashlib.pbkdf2_hmac(
                "sha256", password.encode(), pw_salt.encode(), int(iterations)
            ).hex()
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(calc, digest)
    return hmac.compare_digest(sha256(password), stored)


# region 库结构初始化（v2）：旧库自动备份后重建
def _db_path() -> str:
    """数据库文件路径（相对当前工作目录，与 v1 一致）。"""
    return db.database


def _current_user_version() -> int:
    row = db.execute_sql("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def _archive_legacy_db() -> str:
    """把 v1 结构的数据文件改名备份（不做数据迁移），返回备份文件名。"""
    db.close()
    path = _db_path()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.legacy-{stamp}"
    if os.path.exists(path):
        os.replace(path, backup)
    db.connect()
    print(
        "[api] 检测到旧版数据库结构：本版本不做数据迁移，"
        f"旧库已备份为 {os.path.basename(backup)}，正在重建空库。"
    )
    return backup


def _create_partial_indexes() -> None:
    """举报去重从「应用层先查再插」升级为数据库约束。

    同一举报人对同一目标的 pending 举报只能有一条；partial unique index 只约束
    pending 状态，已处理/已驳回后可以重新举报。
    """
    db.execute_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_report_pending_post "
        "ON _report(reporter_id, post_id) "
        "WHERE comment_id IS NULL AND status = 'pending'"
    )
    db.execute_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_report_pending_comment "
        "ON _report(reporter_id, comment_id) "
        "WHERE comment_id IS NOT NULL AND status = 'pending'"
    )


def init_database() -> None:
    """建连 + 建表 + 结构版本校验（导入本模块时自动执行一次）。"""
    db.connect(reuse_if_open=True)
    legacy = db.table_exists("_post") and _current_user_version() != SCHEMA_VERSION
    if legacy:
        _archive_legacy_db()
    db.create_tables(ALL_MODELS, safe=True)
    _create_partial_indexes()
    if _current_user_version() != SCHEMA_VERSION:
        db.execute_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")


init_database()

# 启动引导：创建管理员账号（凭据来自 secrets / 环境变量 / config.toml）
_admin = admin_credentials()
if _admin.get("userid") and _admin.get("password"):
    if _User.get_or_none(_User.userid == _admin["userid"]) is None:
        _User.create(
            userid=_admin["userid"],
            username=_admin.get("username") or _admin["userid"],
            password=hash_password(_admin["password"]),
            role=admin,
            created_at=now_str(),
        )
# endregion


# region 数据库接口
class User:
    def register(
        self,
        userid: str,
        username: str,
        password: str,
        role: str = user,
    ) -> bool:
        """注册账号，如果成功则返回`True`。

        校验放在数据层：页面上的校验只负责提示，绕过页面直接调用
        （脚本 / 未来的 API）时同样要被挡住。
        """
        userid = (userid or "").strip()
        username = (username or "").strip()
        if not _VALID_USERID_RE.fullmatch(userid):
            return False
        if not username or len(username) > 20:
            return False
        if not is_valid_password(password):
            return False
        if role not in (admin, user):
            return False
        if not self.exists(userid):  # 避免重复用户ID
            try:
                _User.create(
                    userid=userid,
                    username=username,
                    password=hash_password(password),
                    role=role,
                    created_at=now_str(),
                )
            except UNIQUE_VIOLATION:  # 并发注册同一 ID：主键冲突
                return False
            return True
        else:
            return False

    def login(self, userid: str | None, password: str | None) -> bool:
        """登录账号，如果用户名与其密码对应上则返回`True`"""
        if userid and password:
            user = _User.get_or_none(_User.userid == userid)
            if user and verify_password(password, user.password):
                # 旧版 sha256 密码校验成功后自动升级为 PBKDF2
                if not user.password.startswith("pbkdf2$"):
                    user.password = hash_password(password)
                    user.save()
                return True
        return False

    check = login

    def check_admin(
        self,
        secret_key: str | None,
        *,
        admin_userid: str | None = None,
        admin_password: str | None = None,
    ) -> bool | None:
        """检查是否为管理员"""
        if self.check(admin_userid, admin_password):
            if self.get_config(admin_userid)["role"] == admin:
                return True
        if not secret_key:
            return None
        user = _User.get_or_none(_User.secret_key_hash == sha256(secret_key))
        if user is None or user.role != admin:
            return False
        # 过期时间格式固定，字典序即时间序
        return bool(user.secret_key_expires_at) and (
            user.secret_key_expires_at >= now_str()
        )

    @staticmethod
    def exists(userid: str) -> bool:
        """检查账号是否存在"""
        return _User.get_or_none(_User.userid == userid)

    @staticmethod
    def get_all() -> list[dict[str, str]]:
        """数据整理成`list[dict[str, str]]`"""
        return list(_User.select().dicts())

    @staticmethod
    def get_config(userid: str | None) -> dict[str, str] | None:
        """获取用户配置"""
        user = _User.get_or_none(_User.userid == userid)
        if user:
            return {
                "userid": user.userid,
                "username": user.username,
                "created_at": user.created_at,
                "description": user.description,
                "role": user.role,
                "avatar": user.avatar,
            }
        return None

    @staticmethod
    def get_configs(userids: typing.Iterable[str]) -> dict[str, dict[str, str]]:
        """批量获取用户配置（单次查询），返回 {userid: config}"""
        ids = {uid for uid in userids if uid}
        if not ids:
            return {}
        return {
            u.userid: {
                "userid": u.userid,
                "username": u.username,
                "created_at": u.created_at,
                "description": u.description,
                "role": u.role,
                "avatar": u.avatar,
            }
            for u in _User.select().where(_User.userid.in_(ids))
        }

    @staticmethod
    def issue_secret_key(userid: str, password: str | None) -> str | None:
        """校验密码后签发新的会话密钥（明文只交给调用方存进加密 Cookie）。

        签发即轮换：该用户此前所有会话密钥立即失效（改密 / 换设备登录后
        旧的管理员权限随之消失）。库里只留哈希与过期时间。
        """
        if not userid or not password:
            return None
        user = _User.get_or_none(_User.userid == userid)
        if user is None or not verify_password(password, user.password):
            return None
        key = generate_sk_key(16)
        expires = (
            datetime.datetime.now() + datetime.timedelta(days=SECRET_KEY_TTL_DAYS)
        ).strftime(NOW_FORMAT)
        _User.update(
            secret_key_hash=sha256(key), secret_key_expires_at=expires
        ).where(_User.userid == userid).execute()
        return key

    def modify(
        self,
        # 验证信息
        userid: str,
        password: str,
        # 基本信息
        username: str | None = None,
        description: str | None = None,
        avatar: str | None = None,
        # 高级选项
        new_password: str | None = None,
        # 高级权限
        admin_secret_key: str | None = None,
    ) -> bool:
        """修改用户配置"""
        if self.check(userid, password) or self.check_admin(admin_secret_key):
            user = _User.get_or_none(_User.userid == userid)
            if user is None:
                return False
            if username:
                user.username = username
            if description:
                user.description = description
            if avatar:
                # 只接受「默认头像」或 avatars/ 下真实存在的 <uuid>.jpg，
                # 否则 avatar 字段可以被写成任意绝对路径（读任意文件）。
                if not is_valid_avatar_name(avatar):
                    return False
                user.avatar = avatar
            if new_password:
                if not is_valid_password(new_password):
                    return False
                user.password = hash_password(new_password)
            user.save()
            return True
        return False

    def is_admin(self, userid: str | None, password: str | None) -> bool:
        """该账号当前是否是管理员（密码当场校验，查库取 role）。

        管理员页面的动作优先用这条路径：登录态里的 userid/password 由
        main.py 每次 rerun 校验（api2.check_by_state），比 Cookie 里的
        secretkey 更可靠（密钥轮换/过期都不会让后台操作失效）。
        """
        if not userid or not password:
            return False
        cfg = self.get_config(userid)
        return bool(cfg) and cfg["role"] == admin and self.check(userid, password)

    def set_role(
        self,
        admin_secret_key: str | None,
        target_userid: str,
        role: str,
        *,
        admin_userid: str | None = None,
        admin_password: str | None = None,
    ) -> bool:
        """修改用户角色——**唯一的 role 写入口**，必须持有效管理员会话密钥。

        资料接口 User.modify 不再接受 role 参数，避免「知道密码就能自提权」。
        """
        if role not in (admin, user):
            return False
        if not (
            self.check_admin(admin_secret_key)
            or self.is_admin(admin_userid, admin_password)
        ):
            return False
        return (
            _User.update(role=role).where(_User.userid == target_userid).execute() > 0
        )

    @staticmethod
    def count() -> int:
        """用户总数"""
        return _User.select().count()

    def delete(
        self,
        userid: str,
        password: str | None,
        *,
        admin_secret_key: str | None = None,
        admin_userid: str | None = None,
        admin_password: str | None = None,
    ) -> bool:
        """删除用户；需要用户密码、管理员会话或管理员密钥。"""
        if (
            self.check(userid, password)
            or self.check_admin(admin_secret_key)
            or self.is_admin(admin_userid, admin_password)
        ):
            try:
                user = _User.get_or_none(_User.userid == userid)
                if not user:
                    return False
                user.delete_instance()
                return True
            except Exception:
                return False


def _sync_tags(post_id: int, tags: typing.Iterable[str] | None) -> None:
    """用给定标签集合整体替换文章标签（保持用户选择顺序，自动去重）。"""
    names: list[str] = []
    for name in tags or []:
        name = (name or "").strip()
        if name and name not in names:
            names.append(name)
    _PostTag.delete().where(_PostTag.post == post_id).execute()
    for position, name in enumerate(names):
        tag, _ = _Tag.get_or_create(name=name)
        _PostTag.create(post=post_id, tag=tag, position=position)


def _sync_attachments(post_id: int, attachments: typing.Iterable[dict]) -> None:
    """把发布时上传的附件元数据写入 _attachment（文件本体已由 Attachment.save 落盘）。"""
    for att in attachments or []:
        stored_name = att.get("saved_name")
        if not stored_name:
            continue
        _Attachment.create(
            post=post_id,
            original_name=att.get("original_name") or stored_name,
            stored_name=stored_name,
            mime=att.get("type") or "",
            size=int(att.get("size") or 0),
            created_at=now_str(),
        )


def _comment_id_at(postid: int, comment_index: int) -> int | None:
    """按「文章内楼层次序」取评论 ID（与 _comments_map 的排序一致）。"""
    if comment_index < 0:
        return None
    return (
        _Comment.select(_Comment.id)
        .where(_Comment.post == postid)
        .order_by(_Comment.created_at, _Comment.id)
        .offset(comment_index)
        .limit(1)
        .scalar()
    )


class Post:
    """文章接口。所有返回值保持 v1 键名（authorid / tags / attachments / comments）。"""

    # region 写操作
    @staticmethod
    def publish(
        authorid: str,
        title: str,
        content: str,
        attachments: typing.List[dict[str, str]],
        attpassword: str | None = None,
        tags: typing.List[str] = None,
    ) -> int:
        """发布文章，返回新文章 ID（附件与标签在同一事务内写入）。"""
        print(f"{authorid}发布了新文章：{title}")
        with db.atomic():
            post = _Post.create(
                author_id=authorid,
                title=title,
                content=content,
                created_at=now_str(),
                attpassword=sha256(attpassword) if attpassword else "",
            )
            _sync_attachments(post.id, attachments)
            _sync_tags(post.id, tags)
        return post.id

    @staticmethod
    def delete(postid: int) -> bool:
        """删除文章。

        评论 / 点赞 / 收藏 / 标签 / 附件由外键级联清理（v1 需手工删评论，其余会留孤儿）；
        磁盘上的附件文件不在这里删，留给管理后台的「孤立附件清理」。
        """
        return _Post.delete().where(_Post.id == postid).execute() > 0

    @staticmethod
    def edit(postid: int, title: str, content: str, tags: typing.List[str] = None) -> bool:
        """编辑文章标题 / 正文 / 标签。"""
        with db.atomic():
            updated = (
                _Post.update(title=title, content=content)
                .where(_Post.id == postid)
                .execute()
            )
            if not updated:
                return False
            if tags is not None:
                _sync_tags(postid, tags)
        return True

    @staticmethod
    def add_view(postid: int) -> None:
        """文章浏览量 +1（单条 UPDATE，不必读回整行）。"""
        _Post.update(views=_Post.views + 1).where(_Post.id == postid).execute()

    # endregion

    # region 读操作
    @staticmethod
    def get_all() -> list[dict]:
        """全部文章（管理后台的图表 / 统计用，含标签）。"""
        return _post_dicts(_Post.select().order_by(_Post.id).dicts())

    @staticmethod
    def _apply_filters(
        query,
        keyword: str = "",
        tag: str | None = None,
        post_ids: typing.Iterable[int] | None = None,
        author: str = "",
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
    ):
        """按关键词 / 标签 / 文章 ID 集合 / 作者 / 发布时间范围过滤（SQL 层，计数与分页共用）。

        - 标签：走 _post_tag 关联表等值匹配（v1 是 JSON.contains，无法走索引）
        - 关键词：标题 / 正文模糊匹配
        - 作者：userid 或用户名模糊匹配
        - 发布时间：created_at 字符串区间（字典序即时间序），起止日期均包含
        """
        if keyword:
            query = query.where(
                (_Post.title.contains(keyword)) | (_Post.content.contains(keyword))
            )
        if tag:
            tagged = (
                _PostTag.select(_PostTag.post)
                .join(_Tag, on=(_PostTag.tag == _Tag.id))
                .where(_Tag.name == tag)
            )
            query = query.where(_Post.id.in_(tagged))
        if post_ids is not None:
            query = query.where(_Post.id.in_(list(post_ids)))
        if author:
            author_ids = [
                u.userid
                for u in _User.select().where(
                    (_User.userid.contains(author)) | (_User.username.contains(author))
                )
            ]
            if author_ids:
                query = query.where(_Post.author_id.in_(author_ids))
            else:
                # 无匹配作者 → 查询恒为空
                query = query.where(_Post.id == -1)
        if start_date is not None:
            query = query.where(
                _Post.created_at >= start_date.strftime("%Y-%m-%d") + " 00:00:00"
            )
        if end_date is not None:
            query = query.where(
                _Post.created_at <= end_date.strftime("%Y-%m-%d") + " 23:59:59"
            )
        return query

    @staticmethod
    def count_filtered(
        keyword: str = "",
        tag: str | None = None,
        post_ids: typing.Iterable[int] | None = None,
        author: str = "",
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
    ) -> int:
        """按过滤条件统计文章总数（分页用，与列表口径一致）。"""
        return Post._apply_filters(
            _Post.select(), keyword, tag, post_ids, author, start_date, end_date
        ).count()

    @staticmethod
    def get_filtered_paginate(
        keyword: str = "",
        tag: str | None = None,
        post_ids: typing.Iterable[int] | None = None,
        author: str = "",
        page: int = 1,
        page_size: int = 5,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
        sort_by: str = "date",
    ) -> list[dict]:
        """按条件过滤后分页取文章。

        ``sort_by``：``date``（默认）/ ``views`` / ``likes`` / ``bookmarks``。
        四种排序全部下推到 SQL，且排序键都有索引（v1 的 likes/bookmarks 会先全表
        聚合计数、再把全部文章拉回 Python 排序切片）。
        """
        query = Post._apply_filters(
            _Post.select(), keyword, tag, post_ids, author, start_date, end_date
        )

        if sort_by == "views":
            order_by = (_Post.views.desc(), _Post.id.desc())
        elif sort_by == "likes":
            order_by = (_Post.like_count.desc(), _Post.id.desc())
        elif sort_by == "bookmarks":
            order_by = (_Post.bookmark_count.desc(), _Post.id.desc())
        else:
            order_by = (_Post.created_at.desc(), _Post.id.desc())

        rows = query.order_by(*order_by).paginate(page, page_size).dicts()
        return _post_dicts(rows)

    @staticmethod
    def get_by_author(authorid: str) -> list[dict]:
        """某用户发布的全部文章（发布时间倒序），用于个人主页。"""
        rows = (
            _Post.select()
            .where(_Post.author_id == authorid)
            .order_by(_Post.created_at.desc(), _Post.id.desc())
            .dicts()
        )
        return _post_dicts(rows)

    @staticmethod
    def get_author_stats(authorid: str) -> dict:
        """统计某作者的文章数 / 总浏览量 / 总获赞 / 总获藏（单条聚合查询）。"""
        row = (
            _Post.select(
                fn.COUNT(_Post.id).alias("post_count"),
                fn.COALESCE(fn.SUM(_Post.views), 0).alias("views"),
                fn.COALESCE(fn.SUM(_Post.like_count), 0).alias("likes"),
                fn.COALESCE(fn.SUM(_Post.bookmark_count), 0).alias("bookmarks"),
            )
            .where(_Post.author_id == authorid)
            .dicts()
            .first()
        ) or {}
        return {
            "post_count": row.get("post_count") or 0,
            "views": row.get("views") or 0,
            "likes": row.get("likes") or 0,
            "bookmarks": row.get("bookmarks") or 0,
        }

    @staticmethod
    def get(_id: int) -> dict | None:
        """文章详情（评论为 json 字符串列表，保持 v1 契约）。"""
        row = _Post.select().where(_Post.id == _id).dicts().get_or_none()
        if not row:
            return None
        return _post_dicts([row], with_comments=True)[0]

    @staticmethod
    def get_last_comments(posts: typing.Iterable[dict]) -> dict[int, dict]:
        """批量取每篇文章的最新一条评论（单次查询），返回 {post_id: comment}。"""
        return _last_comment_map([p["id"] for p in posts if p.get("id")])

    @staticmethod
    def count() -> int:
        """文章总数"""
        return _Post.select().count()

    # endregion

    # region 评论（按「文章内楼层次序」定位，v1 用 JSON 数组下标实现同样的语义）
    @staticmethod
    def add_comment(postid: int, userid: str, content: str) -> int | None:
        """添加评论，返回评论 ID；文章不存在时返回 None。"""
        with db.atomic():
            if _Post.get_or_none(_Post.id == postid) is None:
                return None
            comment = _Comment.create(
                post=postid,
                user_id=userid,
                content=content,
                created_at=now_str(),
            )
            _Post.update(comment_count=_Post.comment_count + 1).where(
                _Post.id == postid
            ).execute()
        return comment.id

    @staticmethod
    def edit_comment(postid: int, comment_index: int, content: str) -> bool:
        """编辑评论（comment_index 为文章内楼层次序，0 起）。"""
        cid = _comment_id_at(postid, comment_index)
        if cid is None:
            return False
        return _Comment.update(content=content).where(_Comment.id == cid).execute() > 0

    @staticmethod
    def delete_comment(postid: int, comment_index: int) -> bool:
        """删除评论（comment_index 为文章内楼层次序，0 起）。"""
        cid = _comment_id_at(postid, comment_index)
        if cid is None:
            return False
        with db.atomic():
            deleted = _Comment.delete().where(_Comment.id == cid).execute()
            if deleted:
                _Post.update(comment_count=_Post.comment_count - 1).where(
                    _Post.id == postid
                ).execute()
        return bool(deleted)

    # endregion

    @staticmethod
    def recount(postid: int) -> dict | None:
        """按明细表重算某篇文章的冗余计数器（数据修复 / 迁移校验用）。"""
        post = _Post.get_or_none(_Post.id == postid)
        if post is None:
            return None
        likes = _Like.select().where(_Like.post == postid).count()
        bookmarks = _Bookmark.select().where(_Bookmark.post == postid).count()
        comments = _Comment.select().where(_Comment.post == postid).count()
        _Post.update(
            like_count=likes, bookmark_count=bookmarks, comment_count=comments
        ).where(_Post.id == postid).execute()
        return {"likes": likes, "bookmarks": bookmarks, "comments": comments}


@st.cache_data(ttl="30m", max_entries=256, show_spinner=False)
def _read_attachment_file(saved_name: str) -> bytes:
    """读附件字节并按文件名缓存（附件名是 uuid、内容不可变，缓存永远安全）。

    列表页/详情页/打包下载都会重复读同一批文件；不缓存时每次 rerun 都要
    把整篇帖子的附件从磁盘读一遍（多图帖子可达数 MB）。
    """
    file_path = os.path.join(ATTACHMENTS_DIR, saved_name)  # NOQA
    try:
        with open(file_path, "rb") as f:
            return f.read()
    except OSError:
        return b""


@st.cache_data(ttl="30m", max_entries=64, show_spinner=False)
def build_attachments_zip(
    post_id: int, entries: tuple[tuple[str, str], ...]
) -> bytes:
    """把一篇帖子的附件打包成 ZIP 字节流（按帖子 + 附件名缓存）。

    entries 是 ((saved_name, original_name), ...) 的元组——用元组而不是
    list，是因为 st.cache_data 的缓存键要求可哈希。附件名是 uuid、内容
    不可变，所以缓存不会过期到错误内容。
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for saved_name, original_name in entries:
            data = _read_attachment_file(saved_name)
            if data:
                zf.writestr(original_name or saved_name, data)
    return buffer.getvalue()


class Attachment:
    @staticmethod
    def save(uploaded_file) -> dict:
        """
        保存上传的文件到本地磁盘，返回文件的元数据。

        参数:
            uploaded_file: streamlit 的上传文件对象 (UploadedFile)

        返回:
            dict: 包含文件元数据的字典

        校验：空文件 / 超过 MAX_ATTACHMENT_BYTES 一律拒绝（抛 ValueError，
        由页面捕获成友好提示）。扩展名只保留字母数字，避免落盘成可执行/可
        渲染的文件名。
        """
        # 读取文件二进制数据
        file_bytes = uploaded_file.getvalue()
        size = len(file_bytes)
        if size == 0:
            raise ValueError("文件为空")
        if size > MAX_ATTACHMENT_BYTES:
            raise ValueError(
                f"文件超过上限 {format_size(MAX_ATTACHMENT_BYTES)}"
            )

        # 生成唯一文件名（保留原始扩展名，但只接受字母数字扩展名）
        ext = os.path.splitext(os.path.basename(uploaded_file.name or ''))[1].lower()
        if not _ATTACHMENT_EXT_RE.fullmatch(ext):
            ext = ''
        unique_name = f"{uuid.uuid4().hex}{ext}"

        # 保存文件到 attachments 目录
        file_path = os.path.join(ATTACHMENTS_DIR, unique_name)  # NOQA
        with open(file_path, "wb") as f:
            f.write(file_bytes)

        # 返回元数据（不包含文件内容，只存路径）
        return {
            "original_name": uploaded_file.name,  # 原始文件名
            "saved_name": unique_name,  # 存储在磁盘的文件名
            "type": uploaded_file.type,  # MIME 类型（如 image/png）
            "size": size,  # 文件大小（字节，按实际字节数而非声明值）
            "path": unique_name,  # 相对路径（用于读取时拼接）
        }

    @staticmethod
    @st.cache_data(ttl="10m", max_entries=256)
    def get_thumbnail_bytes(saved_name: str, max_width: int = 300) -> bytes:
        """生成图片附件的缩略图（JPEG），结果按文件名缓存 10 分钟。

        非图片或损坏的文件原样返回；文件缺失返回空字节。
        """
        file_bytes = Attachment.get_file(saved_name)
        if not file_bytes:
            return b""
        try:
            img = Image.open(io.BytesIO(file_bytes))
        except Exception:
            return file_bytes
        if img.mode not in ("RGB", "L", "CMYK"):
            # JPEG 不支持 P（调色板）/ RGBA / LA / I / F / 1 等模式，统一转 RGB
            img = img.convert("RGB")
        if img.width > max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), Image.LANCZOS)  # NOQA
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()

    @staticmethod
    def get_thumbnail_base64(saved_name: str, max_width: int = 300) -> str:
        return base64.b64encode(
            Attachment.get_thumbnail_bytes(saved_name, max_width)
        ).decode()

    @staticmethod
    def get_file(saved_name: str) -> bytes:
        """
        根据保存的文件名读取附件二进制数据。

        参数:
            saved_name: 数据库里存的文件名

        返回:
            bytes: 文件的二进制数据，文件不存在时返回空字节
        """
        # 防目录穿越：与头像读取一致，只取文件名部分
        return _read_attachment_file(os.path.basename(saved_name or ""))


class Like:
    """文章点赞：同一用户对同一文章只能点一次（主键 post_id + user_id 保证）。

    _post.like_count 与明细在同一个事务内增减，排序因此可以直接走索引。
    """

    @staticmethod
    def toggle(postid: int, userid: str) -> bool:
        """切换点赞状态，返回点赞后的状态（True = 已点赞）。"""
        try:
            with db.atomic():
                like = _Like.get_or_none(
                    (_Like.post == postid) & (_Like.user_id == userid)
                )
                if like:
                    like.delete_instance()
                    _Post.update(like_count=_Post.like_count - 1).where(
                        _Post.id == postid
                    ).execute()
                    return False
                _Like.create(post=postid, user_id=userid, created_at=now_str())
                _Post.update(like_count=_Post.like_count + 1).where(
                    _Post.id == postid
                ).execute()
                return True
        except UNIQUE_VIOLATION:
            # 文章已被删除：外键拒绝写入
            return False

    @staticmethod
    def is_liked(postid: int, userid: str) -> bool:
        """用户是否已点赞该文章。"""
        return (
            _Like.get_or_none((_Like.post == postid) & (_Like.user_id == userid))
            is not None
        )

    @staticmethod
    def count(postid: int) -> int:
        """文章的点赞总数（读 _post 冗余计数器，主键点查）。"""
        return _Post.select(_Post.like_count).where(_Post.id == postid).scalar() or 0


class Bookmark:
    """文章收藏：同一用户对同一文章只能收藏一次（主键 post_id + user_id 保证）。"""

    @staticmethod
    def toggle(postid: int, userid: str) -> bool:
        """切换收藏状态，返回收藏后的状态（True = 已收藏）。"""
        try:
            with db.atomic():
                bm = _Bookmark.get_or_none(
                    (_Bookmark.post == postid) & (_Bookmark.user_id == userid)
                )
                if bm:
                    bm.delete_instance()
                    _Post.update(bookmark_count=_Post.bookmark_count - 1).where(
                        _Post.id == postid
                    ).execute()
                    return False
                _Bookmark.create(post=postid, user_id=userid, created_at=now_str())
                _Post.update(bookmark_count=_Post.bookmark_count + 1).where(
                    _Post.id == postid
                ).execute()
                return True
        except UNIQUE_VIOLATION:
            return False

    @staticmethod
    def is_bookmarked(postid: int, userid: str) -> bool:
        """用户是否已收藏该文章。"""
        return (
            _Bookmark.get_or_none(
                (_Bookmark.post == postid) & (_Bookmark.user_id == userid)
            )
            is not None
        )

    @staticmethod
    def count(postid: int) -> int:
        """文章的收藏总数（读 _post 冗余计数器）。"""
        return (
            _Post.select(_Post.bookmark_count).where(_Post.id == postid).scalar() or 0
        )

    @staticmethod
    def get_bookmarked_post_ids(userid: str) -> list[int]:
        """获取用户收藏的所有文章 ID（按收藏时间倒序）。"""
        return [
            row["post"]
            for row in _Bookmark.select(_Bookmark.post)
            .where(_Bookmark.user_id == userid)
            .order_by(_Bookmark.created_at.desc())
            .dicts()
        ]


class Report:
    """举报：用户可举报文章或评论，管理员在后台受理处理。

    「举报文章」与「举报评论」靠 comment_id 是否为 NULL 区分。同一举报人对同一
    目标只允许一条 pending 举报（由 partial unique index 保证，见 init_database）。
    """

    @staticmethod
    def add(
        reporter_userid: str,
        *,
        postid: int | None = None,
        commentid: int | None = None,
        target_userid: str = "",
        content_preview: str = "",
        reason: str = "",
    ) -> int | None:
        """提交举报，返回举报 ID；重复举报（同一人对同一目标且未处理）返回 None。

        参数：
            reporter_userid: 举报人 userid
            postid / commentid: 被举报目标（举报文章传 postid，举报评论传 postid+commentid）
            target_userid: 被举报内容作者 userid（便于后台核对）
            content_preview: 被举报内容摘要快照
            reason: 举报理由（必填）
        """
        if not reporter_userid:
            return None
        reason = (reason or "").strip()
        if not reason:
            return None
        try:
            return _Report.create(
                post_id=postid,
                comment_id=commentid,
                target_user_id=target_userid,
                reporter_id=reporter_userid,
                content_preview=(content_preview or "")[:500],
                reason=reason,
                status="pending",
                created_at=now_str(),
            ).id
        except UNIQUE_VIOLATION:
            # 已有同一目标的 pending 举报（唯一索引兜底，避免并发重复提交）
            return None

    @staticmethod
    def get(report_id: int) -> dict | None:
        """获取单条举报详情。"""
        row = _Report.select().where(_Report.id == report_id).dicts().get_or_none()
        return _report_dict(row) if row else None

    @staticmethod
    def get_all(status: str | None = None) -> list[dict]:
        """获取全部举报（可按状态过滤），按时间倒序。"""
        q = _Report.select().order_by(_Report.id.desc())
        if status:
            q = q.where(_Report.status == status)
        return [_report_dict(row) for row in q.dicts()]

    @staticmethod
    def count(status: str | None = None) -> int:
        """举报总数（可按状态过滤）。"""
        q = _Report.select()
        if status:
            q = q.where(_Report.status == status)
        return q.count()

    @staticmethod
    def handle(report_id: int, handled_by: str, action: str, note: str = "") -> bool:
        """处理举报：action 为 handled（已处理）或 dismissed（驳回/误报）。"""
        if action not in ("handled", "dismissed"):
            return False
        updated = (
            _Report.update(
                status=action,
                handled_by=handled_by,
                handled_at=now_str(),
                note=(note or "")[:500],
            )
            .where((_Report.id == report_id) & (_Report.status == "pending"))
            .execute()
        )
        return updated > 0


class Draft:
    """草稿：用户可保存未完成的文章，稍后在发布页继续编辑。"""

    @staticmethod
    def save(
        userid: str,
        title: str = "",
        content: str = "",
        tags: typing.List[str] = None,
        attpassword: str = "",
        draft_id: int | None = None,
    ) -> int | None:
        """保存草稿；传入 draft_id 表示更新已有草稿，否则新建。"""
        if not userid:
            return None
        now = now_str()
        if draft_id:
            d = _Draft.get_or_none(
                (_Draft.id == draft_id) & (_Draft.user_id == userid)
            )
            if d is None:
                # 草稿不存在或不属于该用户：返回 None 让调用方报错，
                # 不要静默新建（否则一次越权 ID 就变成一份新草稿）。
                return None
            d.title = title
            d.content = content
            if tags is not None:
                d.tags = tags
            if attpassword is not None:
                d.attpassword = attpassword
            d.updated_at = now
            d.save()
            return d.id
        return _Draft.create(
            user_id=userid,
            title=title,
            content=content,
            tags=tags or [],
            attpassword=attpassword,
            created_at=now,
            updated_at=now,
        ).id

    @staticmethod
    def get_drafts(userid: str) -> list[dict]:
        """获取某用户的全部草稿（按草稿 ID 倒序）。"""
        rows = (
            _Draft.select()
            .where(_Draft.user_id == userid)
            .order_by(_Draft.id.desc())
            .dicts()
        )
        return [_draft_dict(row) for row in rows]

    @staticmethod
    def get_draft(userid: str, draft_id: int) -> dict | None:
        """获取单条草稿（仅限本人）。"""
        row = (
            _Draft.select()
            .where((_Draft.id == draft_id) & (_Draft.user_id == userid))
            .dicts()
            .get_or_none()
        )
        return _draft_dict(row) if row else None

    @staticmethod
    def delete(userid: str, draft_id: int) -> bool:
        """删除草稿（仅限本人）。"""
        return (
            _Draft.delete()
            .where((_Draft.id == draft_id) & (_Draft.user_id == userid))
            .execute()
            > 0
        )

    @staticmethod
    def count(userid: str) -> int:
        """某用户的草稿数。"""
        return _Draft.select().where(_Draft.user_id == userid).count()


def save_avatar(uploaded_file) -> dict:
    """
    保存上传的头像到本地磁盘，返回文件的元数据。

    参数:
        uploaded_file: streamlit 的上传文件对象 (UploadedFile)

    返回:
        dict: 包含文件元数据的字典（path 存相对文件名，便于跨机器迁移）
    """
    # 读取文件二进制数据
    file_bytes = uploaded_file.getvalue()

    # 裁剪为 1:1 正方形（取中心区域）并压缩：
    # - 超过 MAX_AVATAR_DIM 的边长缩小到上限（高清头像大幅减体积）
    # - 统一转为 RGB JPEG（q=AVATAR_QUALITY），透明背景以白色填充
    try:
        img = Image.open(io.BytesIO(file_bytes))
        width, height = img.size
        if width != height:
            side = min(width, height)
            left = (width - side) // 2
            top = (height - side) // 2
            img = img.crop((left, top, left + side, top + side))  # NOQA
        if img.width > MAX_AVATAR_DIM:
            img = img.resize((MAX_AVATAR_DIM, MAX_AVATAR_DIM), Image.LANCZOS)  # NOQA
        if img.mode in ("RGBA", "LA", "PA", "P"):
            # 有透明通道时平铺到白色底再转 RGB，避免 JPEG 出现黑底
            rgba = img.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        else:
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=AVATAR_QUALITY)
        file_bytes = buf.getvalue()
    except Exception:
        pass  # 非图片直接原样保存

    # 生成唯一文件名（压缩后统一为 .jpg，与存储内容一致）
    unique_name = f"{uuid.uuid4().hex}.jpg"

    # 保存文件到 avatars 目录
    file_path = os.path.join(AVATARS_DIR, unique_name)  # NOQA
    with open(file_path, "wb") as f:
        f.write(file_bytes)

    # 返回元数据（不包含文件内容，只存相对文件名）
    return {
        "original_name": uploaded_file.name,  # 原始文件名
        "saved_name": unique_name,  # 存储在磁盘的文件名
        "type": "image/jpeg",  # 压缩后统一为 JPEG
        "size": len(file_bytes),  # 文件大小（字节）
        "path": unique_name,  # 相对文件名（用于读取时拼接）
    }


_AVATAR_FILE_RE = re.compile(r"^[0-9a-f]{32}\.jpg$")


def is_valid_avatar_name(avatar_name: str | None) -> bool:
    """头像字段只允许「默认头像」或 avatars/ 下真实存在的 <uuid>.jpg。

    头像名会直接拼进文件路径，如果不做白名单，用户可以把它改成任意
    绝对路径，再由 get_avatar_bytes 读出来（历史 P0：任意文件读取）。
    """
    name = avatar_name or ""
    if name == "default_avatar.jpeg":
        return True
    if not _AVATAR_FILE_RE.fullmatch(name):
        return False
    return os.path.isfile(os.path.join(AVATARS_DIR, name))


def get_avatar_bytes(avatar_name: str) -> bytes:
    """按文件名读取头像二进制（只接受文件名，不再兼容绝对路径）。"""
    name = os.path.basename(avatar_name or "")
    if not name:
        name = "default_avatar.jpeg"
    if name == "default_avatar.jpeg":
        path = os.path.join(DIR, name)
    else:
        path = os.path.join(AVATARS_DIR, name)
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


# endregion


# region 数据导出
def export_users_csv() -> bytes:
    """导出全部用户为 CSV（utf-8-sig，Excel 可直接打开；不含密码与密钥）。"""
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["userid", "username", "created_at", "description", "role"])
    for u in _User.select().dicts():
        writer.writerow(
            [u["userid"], u["username"], u["created_at"], u["description"], u["role"]]
        )
    return output.getvalue().encode("utf-8-sig")


def export_users_json() -> bytes:
    """导出全部用户为 JSON。

    注意：v2 起剔除 ``password``（哈希）与 ``secret_key``（等价管理员凭据），
    与 CSV 导出口径保持一致；v1 的 JSON 导出会把这两列一并写进下载文件。
    """
    import json

    data = [
        {
            "userid": u["userid"],
            "username": u["username"],
            "created_at": u["created_at"],
            "description": u["description"],
            "avatar": u["avatar"],
            "role": u["role"],
        }
        for u in _User.select().dicts()
    ]
    return json.dumps(data, ensure_ascii=False, indent=2, default=str).encode(
        "utf-8-sig"
    )


def export_posts_csv() -> bytes:
    """导出全部文章为 CSV（含标签、附件数、评论数统计列）。"""
    import csv
    import io

    rows = list(_Post.select().order_by(_Post.id).dicts())
    posts = _post_dicts(rows)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "authorid",
            "title",
            "content",
            "created_at",
            "tags",
            "attachments_count",
            "comments_count",
        ]
    )
    for p, row in zip(posts, rows):
        writer.writerow(
            [
                p["id"],
                p["authorid"],
                p["title"],
                p["content"],
                p["created_at"],
                ";".join(p["tags"]),
                len(p["attachments"]),
                row["comment_count"],
            ]
        )
    return output.getvalue().encode("utf-8-sig")


def export_posts_json() -> bytes:
    """导出全部文章为 JSON（评论导出为 ID 列表；剔除附件密码哈希）。"""
    import json

    posts = _post_dicts(_Post.select().order_by(_Post.id).dicts(), with_comments=True)
    data = []
    for p in posts:
        item = dict(p)
        item["comments"] = [json.loads(c)["id"] for c in p["comments"]]
        item.pop("attpassword", None)
        data.append(item)
    return json.dumps(data, ensure_ascii=False, indent=2, default=str).encode(
        "utf-8-sig"
    )


def export_comments_csv() -> bytes:
    """导出全部评论为 CSV。"""
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "postid", "userid", "content", "created_at"])
    for c in (_comment_dict(row) for row in _Comment.select().order_by(_Comment.id).dicts()):
        writer.writerow(
            [c["id"], c["postid"], c["userid"], c["content"], c["created_at"]]
        )
    return output.getvalue().encode("utf-8-sig")


def export_comments_json() -> bytes:
    """导出全部评论为 JSON。"""
    import json

    data = [
        _comment_dict(row)
        for row in _Comment.select().order_by(_Comment.id).dicts()
    ]
    return json.dumps(data, ensure_ascii=False, indent=2, default=str).encode(
        "utf-8-sig"
    )


# endregion


# region 管理员工具
def get_attachment_stats() -> dict:
    """附件统计（总数 / 总大小），单条聚合查询。"""
    row = (
        _Attachment.select(
            fn.COUNT(_Attachment.id).alias("cnt"),
            fn.COALESCE(fn.SUM(_Attachment.size), 0).alias("total"),
        )
        .dicts()
        .first()
    ) or {}
    return {"count": row.get("cnt") or 0, "total_size": row.get("total") or 0}


def get_all_attachments() -> list[dict]:
    """所有附件 + 所属文章标题（后台附件管理表格用）。"""
    rows = (
        _Attachment.select(
            _Attachment.id,
            _Attachment.post,
            _Attachment.original_name,
            _Attachment.stored_name,
            _Attachment.mime,
            _Attachment.size,
            _Post.title.alias("post_title"),
        )
        .join(_Post, on=(_Attachment.post == _Post.id))
        .order_by(_Attachment.id)
        .dicts()
    )
    result = []
    for row in rows:
        item = _attachment_dict(row)
        item["post_id"] = row["post"]
        item["post_title"] = row["post_title"]
        result.append(item)
    return result


def get_orphaned_attachments() -> list[str]:
    """找出 attachments 目录中未被任何文章引用的孤立文件。"""
    referenced = {
        row["stored_name"]
        for row in _Attachment.select(_Attachment.stored_name).dicts()
    }
    all_files = set(os.listdir(ATTACHMENTS_DIR))
    return [
        f
        for f in all_files
        if f not in referenced and os.path.isfile(os.path.join(ATTACHMENTS_DIR, f))
    ]


def delete_orphaned_attachments() -> int:
    """删除所有孤立附件文件，返回删除数量。"""
    orphans = get_orphaned_attachments()
    for f in orphans:
        os.remove(os.path.join(ATTACHMENTS_DIR, f))
    return len(orphans)


def get_comment_summary() -> list[dict]:
    """全部评论摘要（含所属文章标题与楼层次序），后台评论管理用。"""
    post_titles = {p.id: p.title for p in _Post.select(_Post.id, _Post.title)}
    rows = (
        _Comment.select()
        .order_by(_Comment.post, _Comment.created_at, _Comment.id)
        .dicts()
    )
    counters: dict[int, int] = {}
    result = []
    for row in rows:
        pid = row["post"]
        index = counters.get(pid, 0)
        counters[pid] = index + 1
        result.append(
            {
                "comment_id": row["id"],
                "post_id": pid,
                "post_title": post_titles.get(pid, "(已删除)"),
                "comment_index": index,
                "userid": row["user_id"],
                "content": row["content"],
                "created_at": row["created_at"],
            }
        )
    return result


def delete_comment_by_id(comment_id: int) -> bool:
    """按 comment_id 删除评论，并同步文章的评论计数。"""
    with db.atomic():
        comment = _Comment.get_or_none(_Comment.id == comment_id)
        if not comment:
            return False
        postid = comment.post_id
        comment.delete_instance()
        _Post.update(comment_count=_Post.comment_count - 1).where(
            _Post.id == postid
        ).execute()
    return True


def search_comments(keyword: str) -> list[dict]:
    """按内容或用户 ID 搜索评论（时间倒序）。"""
    rows = (
        _Comment.select()
        .where(
            (_Comment.content.contains(keyword))
            | (_Comment.user_id.contains(keyword))
        )
        .order_by(_Comment.id.desc())
        .dicts()
    )
    return [_comment_dict(row) for row in rows]


def get_orphaned_comments() -> list[dict]:
    """找出所属文章已不存在的孤立评论。

    v2 用外键 + ``ON DELETE CASCADE``，正常写入不会产生孤儿；函数保留用于检查
    历史遗留数据（外键被临时关闭或旧库导出的数据）。
    """
    rows = (
        _Comment.select()
        .where(_Comment.post_id.not_in(_Post.select(_Post.id)))
        .dicts()
    )
    return [_comment_dict(row) for row in rows]


def delete_orphaned_comments() -> int:
    """删除所有孤立评论，返回删除数量。"""
    rows = (
        _Comment.select(_Comment.id)
        .where(_Comment.post_id.not_in(_Post.select(_Post.id)))
        .dicts()
    )
    ids = [row["id"] for row in rows]
    for cid in ids:
        _Comment.delete().where(_Comment.id == cid).execute()
    return len(ids)


# region 启动引导：自动发布迎新 / 社区规范公告
# 定稿标题作为「是否已发布」的判定依据：管理员账号尚未发布同名文章时发布一次；
# 管理员手动删除后，下次启动会重新生成，重启不会产生重复公告。
# 迎新介绍与社区规范合并为同一篇文章发布，保持连贯、便于阅读。
ANNOUNCEMENT_POST_TITLE = "欢迎来到 FreeLake · 社区规范"


def bootstrap_announcements() -> None:
    """自动发布「迎新 + 社区规范」合订公告（幂等）。

    逻辑与「启动引导：创建管理员账号」一致：在模块导入（站点启动）时执行，
    用「管理员 + 定稿标题」判断是否已存在，避免每次启动重复发布。
    """
    admin_id = _admin.get("userid")
    # 仅当管理员账号确实已存在时才以此身份发布公告（作为作者）。
    # 这样既符合语义，也兼容测试环境（conftest 提供的 admin 无密码、
    # 不会真正创建账号，避免导入测试库时误写入公告文章）。
    if not admin_id or _User.get_or_none(_User.userid == admin_id) is None:
        return
    if _Post.get_or_none(
        (_Post.author_id == admin_id) & (_Post.title == ANNOUNCEMENT_POST_TITLE)
    ):
        return

    Post.publish(
        authorid=admin_id,
        title=ANNOUNCEMENT_POST_TITLE,
        content="\n".join(
            [
                "# 🎉 欢迎加入 FreeLake",
                "",
                "FreeLake 是一个属于大家的校园交流社区。在这里，你可以分享校园生活的点滴、",
                "交流学习经验、发布二手与寻物信息，也可以找到志同道合的伙伴。",
                "",
                "## 你可以做什么",
                "- **发布文章**：分享你的见闻、经验与想法",
                "- **点赞 & 收藏**：支持你喜欢的帖子，方便日后回看",
                "- **评论互动**：与同学们一起讨论交流",
                "- **附带上传**：图文并茂，让内容更丰富",
                "",
                "## 开始使用",
                "1. 注册账号并登录",
                "2. 在「发布文章」页面写点东西分享给大家",
                "3. 到首页逛逛，看看大家都在聊什么",
                "",
                "---",
                "",
                "# 📜 社区规范",
                "",
                "为了维护友善、有序的交流环境，请每位成员遵守以下规范。",
                "",
                "## 一、基本守则",
                "1. 尊重他人，友善交流，禁止辱骂、人身攻击与歧视性言论。",
                "2. 遵守法律法规与校规校纪，不发布违法、违规内容。",
                "3. 保护个人隐私，未经允许不得公开他人隐私信息。",
                "",
                "## 二、内容规范",
                "1. 发布内容须与主题相关，请勿刷屏、灌水。",
                "2. 广告、外链推广等商业信息请提前联系管理员。",
                "3. 转载内容请注明出处，尊重原创。",
                "",
                "## 三、违规处理",
                "- 违反规范的内容将被删除；多次违规者将被限制或封禁账号。",
                "- 如发现违规内容，欢迎向管理员举报。",
                "",
                "感谢每一位成员的理解与配合，让我们一起把 FreeLake 建设得更好！💙",
            ]
        ),
        attachments=[],
        tags=announcement_tags,
    )


bootstrap_announcements()


# endregion
