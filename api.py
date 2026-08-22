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
import secrets
import typing
import uuid

import streamlit as st
import toml
from peewee import *
from PIL import Image
from playhouse.mysql_ext import JSONField  # NOQA

from const import admin, user

db = SqliteDatabase("data.db")
salt = "freelake"

# 附件存储目录（在项目根目录下创建 attachments 文件夹）
DIR = os.path.dirname(__file__)
ATTACHMENTS_DIR = os.path.join(DIR, "attachments")  # NOQA
AVATARS_DIR = os.path.join(DIR, "avatars")  # NOQA

os.makedirs(ATTACHMENTS_DIR, exist_ok=True)
os.makedirs(AVATARS_DIR, exist_ok=True)


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


def execute_sql(query: str) -> list[dict[str, str]]:
    """执行只读 SQL 查询（仅允许 SELECT / WITH / EXPLAIN / PRAGMA）。

    管理后台的 SQL 工具只应进行查询，禁止 DELETE / UPDATE / DROP 等
    写操作，防止误操作破坏数据。
    """
    stripped = (query or "").lstrip().rstrip(";").strip()
    head = stripped.split(None, 1)[0].upper() if stripped else ""
    if head not in ("SELECT", "WITH", "EXPLAIN", "PRAGMA"):
        raise ValueError("仅允许只读查询：SELECT / WITH / EXPLAIN / PRAGMA")
    # 注意：必须用 execute_sql（原生 SQL）；db.execute 会把字符串当作参数绑定
    cursor = db.execute_sql(stripped)
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


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
class BaseModel(Model):
    class Meta:
        database = db


class _User(BaseModel):
    """用于存储用户的表"""

    userid = CharField()
    username = CharField()
    password = CharField(max_length=256)  # 哈希加密过的密码
    created_at = CharField()
    description = TextField(default="这个用户有点懒，什么也没留下~")
    avatar = CharField(default="default_avatar.jpeg")  # 头像文件地址
    role = CharField(default="user")
    # 注意：default 必须传可调用对象（而非调用结果），否则所有用户会共用同一个密钥
    secret_key = CharField(max_length=100, default=lambda: generate_sk_key(16))

    def __str__(self):
        return self.userid


class _Post(BaseModel):
    """用于存储文章的表"""

    id = AutoField()  # 数据库自增主键（不再手工分配）
    authorid = CharField()
    title = TextField()
    content = TextField()
    created_at = CharField()
    attachments = JSONField(default=list)
    attpassword = CharField(max_length=256, default="")
    tags = JSONField(default=list)
    comments = JSONField(default=list)
    views = IntegerField(default=0)  # 浏览量统计


class _Comment(BaseModel):
    """用于存储评论的表"""

    id = AutoField()  # 数据库自增主键（不再手工分配）
    postid = IntegerField()
    userid = CharField()
    content = TextField()
    created_at = CharField()


class _Like(BaseModel):
    postid = IntegerField()
    userid = CharField()


class _Bookmark(BaseModel):
    postid = IntegerField()
    userid = CharField()


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


def fix_duplicate_secret_keys():
    """修复历史 bug：旧版本所有用户共用同一个 secret_key。"""
    seen: set[str] = set()
    for u in _User.select():
        if not u.secret_key or u.secret_key in seen:
            u.secret_key = generate_sk_key(16)
            u.save()
        else:
            seen.add(u.secret_key)


def sha256(value):
    """获取哈希加密加盐后的文本（旧版用户密码与附件密码使用）"""
    return hashlib.sha256((value + salt).encode()).hexdigest()


PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """使用 PBKDF2-SHA256 加盐哈希用户密码，返回自包含的存储字符串。"""
    pw_salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), pw_salt.encode(), PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2${PBKDF2_ITERATIONS}${pw_salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码；兼容旧的 `sha256(明文+salt)` 存储格式。"""
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


db.connect()
db.create_tables([_User, _Post, _Comment, _Like, _Bookmark], safe=True)

# 启动引导：创建管理员账号（凭据来自 secrets / 环境变量 / config.toml）
_admin = admin_credentials()
if _admin.get("userid") and _admin.get("password"):
    if _User.get_or_none(_User.userid == _admin["userid"]) is None:
        _User.create(
            userid=_admin["userid"],
            username=_admin.get("username") or _admin["userid"],
            password=hash_password(_admin["password"]),
            role=admin,
            created_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
fix_duplicate_secret_keys()


# region 数据库接口
class User:
    def register(
        self,
        userid: str,
        username: str,
        password: str,
        role: str = user,
    ) -> bool:
        """注册账号，如果成功则返回`True`"""
        if not self.exists(userid):  # 避免重复用户ID
            _User.create(
                userid=userid,
                username=username,
                password=hash_password(password),
                role=role,
                created_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
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
        user = _User.get_or_none(_User.secret_key == secret_key)
        if user:
            return user.role == admin
        return None

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
    def get_secret_key(userid: str) -> str | None:
        """获取用户密钥"""
        user = _User.get_or_none(_User.userid == userid)
        if user:
            return user.secret_key
        return None

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
        role: str | None = None,
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
                user.avatar = avatar
            if role:
                user.role = role
            if new_password:
                user.password = hash_password(new_password)
            user.save()
            return True
        return False

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
    ) -> bool:
        """删除用户；需要用户密码或管理员密钥验证。"""
        if self.check(userid, password) or self.check_admin(admin_secret_key):
            try:
                user = _User.get_or_none(_User.userid == userid)
                if not user:
                    return False
                user.delete_instance()
                return True
            except Exception:
                return False


class Post:
    @staticmethod
    def publish(
        authorid: str,
        title: str,
        content: str,
        attachments: typing.List[dict[str, str]],
        attpassword: str | None = None,
        tags: typing.List[str] = None,
    ) -> int:
        """发布文章"""
        print(f"{authorid}发布了新文章：{title}")
        if attpassword:
            attpassword = sha256(attpassword)
        else:
            attpassword = ""
        post = _Post.create(
            authorid=authorid,
            title=title,
            created_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            content=content,
            attachments=attachments,
            attpassword=attpassword,
            tags=tags or [],
        )
        return post.id

    @staticmethod
    def delete(postid: int) -> bool:
        """删除文章（同时删除关联的评论）"""
        post = _Post.get_or_none(_Post.id == postid)
        if post:
            _Comment.delete().where(_Comment.postid == postid).execute()
            _Post.delete().where(_Post.id == postid).execute()
            return True
        return False

    @staticmethod
    def edit(postid: int, title: str, content: str, tags: typing.List[str] = None):
        """编辑文章"""
        post = _Post.get_or_none(_Post.id == postid)
        if post:
            post.title = title
            post.content = content
            post.tags = tags or []
            post.save()
            return True
        return False

    @staticmethod
    def get_all() -> list[dict[str, str]]:
        """数据整理成`list[dict[str, str]]`"""
        return list(_Post.select().dicts())

    @staticmethod
    def _apply_filters(
        query,
        keyword: str = "",
        tag: str | None = None,
        post_ids: typing.Iterable[int] | None = None,
    ):
        """按关键词 / 标签 / 文章 ID 集合过滤查询（SQL 层，供计数与分页共用）。

        标签使用 JSONField.contains —— SQLite 上为精确的 JSON 数组元素匹配，
        避免子串误匹配；关键词为标题或内容的模糊搜索。
        """
        if keyword:
            query = query.where(
                (_Post.title.contains(keyword)) | (_Post.content.contains(keyword))
            )
        if tag:
            query = query.where(_Post.tags.contains(tag))
        if post_ids is not None:
            query = query.where(_Post.id.in_(post_ids))
        return query

    @staticmethod
    def count_filtered(
        keyword: str = "",
        tag: str | None = None,
        post_ids: typing.Iterable[int] | None = None,
    ) -> int:
        """按关键词 / 标签 / 文章 ID 集合统计文章总数（用于分页，与列表口径一致）。"""
        return Post._apply_filters(_Post.select(), keyword, tag, post_ids).count()

    @staticmethod
    def get_filtered_paginate(
        keyword: str = "",
        tag: str | None = None,
        post_ids: typing.Iterable[int] | None = None,
        page: int = 1,
        page_size: int = 5,
    ) -> list[dict[str, str]]:
        """按关键词 / 标签 / 文章 ID 集合过滤后分页获取文章（发布时间倒序）。"""
        return (
            Post._apply_filters(_Post.select(), keyword, tag, post_ids)
            .order_by(_Post.id.desc())
            .paginate(page, page_size)
            .dicts()
        )

    @staticmethod
    def get_by_author(authorid: str) -> list[dict[str, str]]:
        """获取某用户发布的全部文章（发布时间倒序），用于个人主页。"""
        return (
            _Post.select()
            .where(_Post.authorid == authorid)
            .order_by(_Post.id.desc())
            .dicts()
        )

    @staticmethod
    def add_view(postid: int) -> None:
        """文章浏览量 +1。"""
        _Post.update(views=_Post.views + 1).where(_Post.id == postid).execute()

    @staticmethod
    def get(_id: int) -> dict | None:
        """获取文章详情，并把评论 ID 列表替换为评论内容（JSON 字符串列表）。

        评论一次性批量查询，避免逐条查询（N+1）；若存在已被删除的评论 ID，
        会自动清理文章的 comments 列表。
        """
        post = _Post.select().where(_Post.id == _id).dicts().get_or_none()
        if not post:
            return None
        comment_ids = post.get("comments") or []
        # 单次查询取出全部评论，避免逐条查询（N+1）
        comments_map = {}
        if comment_ids:
            comments_map = {
                r["id"]: r
                for r in _Comment.select()
                .where(_Comment.id.in_(comment_ids))
                .dicts()
            }
        comments = []
        valid_ids = []
        for cid in comment_ids:
            comment = comments_map.get(cid)
            if comment:
                comments.append(
                    json.dumps(
                        {
                            "userid": comment["userid"],
                            "content": comment["content"],
                            "created_at": comment["created_at"],
                        },
                        ensure_ascii=False,
                    )
                )
                valid_ids.append(cid)
        if len(valid_ids) != len(comment_ids):
            post_obj = _Post.get_or_none(_Post.id == _id)
            if post_obj:
                post_obj.comments = valid_ids
                post_obj.save()
        post["comments"] = comments
        return post

    @staticmethod
    def get_last_comments(posts: typing.Iterable[dict]) -> dict[int, dict]:
        """批量获取每篇文章最新一条评论（单次查询），返回 {post_id: comment}。"""
        ids = []
        for p in posts:
            cids = p.get("comments") or []
            if cids:
                ids.append(cids[-1])
        result: dict[int, dict] = {}
        if not ids:
            return result
        by_id = {r["id"]: r for r in _Comment.select().where(_Comment.id.in_(ids)).dicts()}
        for p in posts:
            cids = p.get("comments") or []
            if cids and cids[-1] in by_id:
                result[p["id"]] = by_id[cids[-1]]
        return result

    @staticmethod
    def add_comment(
        postid: int,
        userid: str,
        content: str,
    ) -> int | None:
        """添加评论，返回评论 ID"""
        comment = _Comment.create(
            postid=postid,
            userid=userid,
            content=content,
            created_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        cid = comment.id
        post = _Post.get_or_none(_Post.id == postid)
        if post:
            post.comments = (post.comments or []) + [cid]
            post.save()
            return cid
        return None

    @staticmethod
    def edit_comment(postid: int, comment_index: int, content: str) -> bool:
        """编辑评论（按文章内评论列表的下标定位）。"""
        post = _Post.get_or_none(_Post.id == postid)
        if post and 0 <= comment_index < len(post.comments):
            cid = post.comments[comment_index]
            updated = (
                _Comment.update(content=content).where(_Comment.id == cid).execute()
            )
            return updated > 0
        return False

    @staticmethod
    def delete_comment(postid: int, comment_index: int) -> bool:
        """删除评论"""
        post = _Post.get_or_none(_Post.id == postid)
        if post and 0 <= comment_index < len(post.comments):
            cid = post.comments[comment_index]
            _Comment.delete().where(_Comment.id == cid).execute()
            del post.comments[comment_index]
            post.save()
            return True
        return False

    @staticmethod
    def count() -> int:
        """文章总数"""
        return _Post.select().count()


class Attachment:
    @staticmethod
    def save(uploaded_file) -> dict:
        """
        保存上传的文件到本地磁盘，返回文件的元数据。

        参数:
            uploaded_file: streamlit 的上传文件对象 (UploadedFile)

        返回:
            dict: 包含文件元数据的字典
        """
        # 读取文件二进制数据
        file_bytes = uploaded_file.getvalue()

        # 生成唯一文件名（保留原始扩展名）
        ext = os.path.splitext(uploaded_file.name)[1]
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
            "size": uploaded_file.size,  # 文件大小（字节）
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
        saved_name = os.path.basename(saved_name or "")
        file_path = os.path.join(ATTACHMENTS_DIR, saved_name)  # NOQA
        try:
            with open(file_path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return b""
        except OSError:
            return b""


class Like:
    """文章点赞：同一用户对同一文章只能点一次（记录在 _Like 表）。"""

    @staticmethod
    def toggle(postid: int, userid: str) -> bool:
        """切换点赞状态，返回点赞后是否处于已点赞状态。"""
        like = _Like.get_or_none((_Like.postid == postid) & (_Like.userid == userid))
        if like:
            like.delete_instance()
            return False
        else:
            _Like.create(postid=postid, userid=userid)
            return True

    @staticmethod
    def is_liked(postid: int, userid: str) -> bool:
        """用户是否已点赞该文章。"""
        return (
            _Like.get_or_none((_Like.postid == postid) & (_Like.userid == userid))
            is not None
        )

    @staticmethod
    def count(postid: int) -> int:
        """文章的点赞总数。"""
        return _Like.select().where(_Like.postid == postid).count()


class Bookmark:
    """文章收藏：同一用户对同一文章只能收藏一次（记录在 _Bookmark 表）。"""

    @staticmethod
    def toggle(postid: int, userid: str) -> bool:
        """切换收藏状态，返回收藏后是否处于已收藏状态。"""
        bm = _Bookmark.get_or_none(
            (_Bookmark.postid == postid) & (_Bookmark.userid == userid)
        )
        if bm:
            bm.delete_instance()
            return False
        else:
            _Bookmark.create(postid=postid, userid=userid)
            return True

    @staticmethod
    def is_bookmarked(postid: int, userid: str) -> bool:
        """用户是否已收藏该文章。"""
        return (
            _Bookmark.get_or_none(
                (_Bookmark.postid == postid) & (_Bookmark.userid == userid)
            )
            is not None
        )

    @staticmethod
    def get_bookmarked_post_ids(userid: str) -> list[int]:
        """获取用户收藏的所有文章 ID。"""
        return [b.postid for b in _Bookmark.select().where(_Bookmark.userid == userid)]


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

    # 生成唯一文件名（保留原始扩展名）
    ext = os.path.splitext(uploaded_file.name)[1]
    unique_name = f"{uuid.uuid4().hex}{ext}"

    # 裁剪为 1:1 正方形（取中心区域）
    try:
        img = Image.open(io.BytesIO(file_bytes))
        if img.mode == "RGBA":
            img = img.convert("RGB")
        width, height = img.size
        if width != height:
            side = min(width, height)
            left = (width - side) // 2
            top = (height - side) // 2
            img = img.crop((left, top, left + side, top + side))  # NOQA
        buf = io.BytesIO()
        img.save(buf, format=img.format or "JPEG")
        file_bytes = buf.getvalue()
    except Exception:
        pass  # 非图片直接原样保存

    # 保存文件到 avatars 目录
    file_path = os.path.join(AVATARS_DIR, unique_name)  # NOQA
    with open(file_path, "wb") as f:
        f.write(file_bytes)

    # 返回元数据（不包含文件内容，只存相对文件名）
    return {
        "original_name": uploaded_file.name,  # 原始文件名
        "saved_name": unique_name,  # 存储在磁盘的文件名
        "type": uploaded_file.type,  # MIME 类型（如 image/png）
        "size": len(file_bytes),  # 文件大小（字节）
        "path": unique_name,  # 相对文件名（用于读取时拼接）
    }


def get_avatar_bytes(avatar_name: str) -> bytes:
    """按名称读取头像二进制，兼容旧数据中保存的绝对路径。"""
    if not avatar_name:
        avatar_name = "default_avatar.jpeg"
    if os.path.isabs(avatar_name):
        path = avatar_name
    elif avatar_name == "default_avatar.jpeg":
        path = os.path.join(DIR, avatar_name)
    else:
        # 防目录穿越：只取文件名部分
        path = os.path.join(AVATARS_DIR, os.path.basename(avatar_name))
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return b""


# endregion


# region 数据导出
def export_users_csv() -> bytes:
    """导出全部用户为 CSV（utf-8-sig，Excel 可直接打开）。"""
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
    """导出全部用户为 JSON。"""
    import json

    data = list(_User.select().dicts())
    return json.dumps(data, ensure_ascii=False, indent=2, default=str).encode(
        "utf-8-sig"
    )


def export_posts_csv() -> bytes:
    """导出全部文章为 CSV（含标签、附件数、评论数统计列）。"""
    import csv
    import io

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
    for p in _Post.select().dicts():
        writer.writerow(
            [
                p["id"],
                p["authorid"],
                p["title"],
                p["content"],
                p["created_at"],
                ";".join(p.get("tags") or []),
                len(p.get("attachments") or []),
                len(p.get("comments") or []),
            ]
        )
    return output.getvalue().encode("utf-8-sig")


def export_posts_json() -> bytes:
    """导出全部文章为 JSON。"""
    import json

    data = list(_Post.select().dicts())
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
    for c in _Comment.select().dicts():
        writer.writerow(
            [c["id"], c["postid"], c["userid"], c["content"], c["created_at"]]
        )
    return output.getvalue().encode("utf-8-sig")


def export_comments_json() -> bytes:
    """导出全部评论为 JSON。"""
    import json

    data = list(_Comment.select().dicts())
    return json.dumps(data, ensure_ascii=False, indent=2, default=str).encode(
        "utf-8-sig"
    )


# endregion


# region 管理员工具
def get_attachment_stats() -> dict:
    """获取所有附件的统计信息（总数/总大小）"""
    total_count = 0
    total_size = 0
    for post in _Post.select().dicts():
        for att in post.get("attachments") or []:
            total_count += 1
            total_size += att.get("size", 0)
    return {"count": total_count, "total_size": total_size}


def get_all_attachments() -> list[dict]:
    """获取所有附件及其所属文章信息"""
    result = []
    for post in _Post.select().dicts():
        for att in post.get("attachments") or []:
            result.append({**att, "post_id": post["id"], "post_title": post["title"]})
    return result


def get_orphaned_attachments() -> list[str]:
    """找出 attachments 目录中未被任何文章引用的孤立文件"""
    referenced = set()
    for post in _Post.select().dicts():
        for att in post.get("attachments") or []:
            saved = att.get("saved_name")
            if saved:
                referenced.add(saved)
    all_files = set(os.listdir(ATTACHMENTS_DIR))
    orphans = [
        f
        for f in all_files
        if f not in referenced and os.path.isfile(os.path.join(ATTACHMENTS_DIR, f))
    ]
    return orphans


def delete_orphaned_attachments() -> int:
    """删除所有孤立附件，返回删除数量"""
    orphans = get_orphaned_attachments()
    for f in orphans:
        os.remove(os.path.join(ATTACHMENTS_DIR, f))
    return len(orphans)


def get_comment_summary() -> list[dict]:
    """获取所有评论的摘要（含所属文章）"""
    posts = {p.id: p for p in _Post.select()}
    result = []
    for comment in _Comment.select().dicts():
        post = posts.get(comment["postid"])
        post_comments = post.comments if post else []
        try:
            comment_index = post_comments.index(comment["id"])
        except ValueError:
            comment_index = -1
        result.append(
            {
                "comment_id": comment["id"],
                "post_id": comment["postid"],
                "post_title": post.title if post else "(已删除)",
                "comment_index": comment_index,
                "userid": comment["userid"],
                "content": comment["content"],
                "created_at": comment["created_at"],
            }
        )
    return result


def delete_comment_by_id(comment_id: int) -> bool:
    """按 comment_id 删除评论，同时从所属文章的 comments 列表中移除"""
    comment = _Comment.get_or_none(_Comment.id == comment_id)
    if not comment:
        return False
    post = _Post.get_or_none(_Post.id == comment.postid)
    if post and post.comments:
        try:
            post.comments.remove(comment_id)
            post.save()
        except ValueError:
            pass
    comment.delete_instance()
    return True


def search_comments(keyword: str) -> list[dict]:
    """按内容或用户ID搜索评论"""
    return list(
        _Comment.select()
        .where(
            (_Comment.content.contains(keyword)) | (_Comment.userid.contains(keyword))
        )
        .order_by(_Comment.id.desc())
        .dicts()
    )


def get_orphaned_comments() -> list[dict]:
    """找出所属文章已被删除的孤立评论"""
    existing_ids = {p.id for p in _Post.select(_Post.id)}
    return [
        c
        for c in _Comment.select().dicts()
        if c["postid"] not in existing_ids
    ]


def delete_orphaned_comments() -> int:
    """删除所有孤立评论，返回删除数量"""
    orphans = get_orphaned_comments()
    for c in orphans:
        _Comment.delete().where(_Comment.id == c["id"]).execute()
    return len(orphans)


# endregion
