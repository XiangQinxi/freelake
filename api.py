import base64
import datetime
import hashlib
import io
import json
import os
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
    """执行SQL查询"""
    return list(db.execute(query).dicts())

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

    def __str__(self):
        return self.userid


class _Post(BaseModel):
    """用于存储文章的表"""

    id = IntegerField()
    authorid = CharField()
    title = TextField()
    content = TextField()
    created_at = CharField()
    attachments = JSONField(default=list)
    attpassword = CharField(max_length=256, default="")
    tags = JSONField(default=list)
    comments = JSONField(default=list)


class _Comment(BaseModel):
    """用于存储评论的表"""

    id = IntegerField()
    postid = IntegerField()
    userid = CharField()
    content = TextField()
    created_at = CharField()
    attachments = JSONField(default=list)
    attpassword = CharField(max_length=256, default="")
# endregion


db.connect()
db.create_tables([_User, _Post, _Comment], safe=True)

def sha256(value):
    """获取哈希加密加盐后的文本"""
    return hashlib.sha256((value + salt).encode()).hexdigest()


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
                password=sha256(password),
                role=role,
                created_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            return True
        else:
            return False

    def login(self, userid: str, password: str) -> bool:
        """登录账号，如果用户名与其密码对应上则返回`True`"""
        if self.exists(userid):
            user = _User.get(_User.userid == userid)
            return user.password == sha256(password)
        return False

    check = login

    @staticmethod
    def exists(userid: str) -> bool:
        """检查账号是否存在"""
        return _User.get_or_none(_User.userid == userid)

    @staticmethod
    def get_all() -> list[dict[str, str]]:
        """数据整理成`list[dict[str, str]]`"""
        return list(_User.select().dicts())

    @staticmethod
    def get_config(userid: str) -> dict[str, str] | None:
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

    def modify_config(
        self, userid: str, username: str, description: str, avatar: str | None = None
    ) -> bool:
        """修改用户配置"""
        if self.check_by_state():
            user = _User.get_or_none(_User.userid == userid)
            user.username = username
            user.description = description
            if avatar:
                user.avatar = avatar
            else:
                user.avatar = user.avatar  # 保持原头像未改变
            user.save()
            return True
        return False

    def modify_role(self, userid: str, secret_key: str, role) -> bool:
        """修改用户角色"""
        if not self.check_by_state():
            return False
        if secret_key != st.secrets["secret_key"]:
            return False
        user = _User.get_or_none(_User.userid == userid)
        if not user:
            return False
        user.role = role
        user.save()
        return True

    def modify_password(self, userid: str, password: str, new_password: str) -> bool:
        """修改密码"""
        if self.login(userid, password):
            user = _User.get(_User.userid == userid)
            user.password = sha256(new_password)
            user.save()
            return True
        return False

    def check_by_state(self) -> bool:
        """检查当前登录状态通过`streamlit.session_state`"""
        return self.check(
            st.session_state.get("userid"), st.session_state.get("password")
        )

    @staticmethod
    def count() -> int:
        return _User.select().count()

    @staticmethod
    def delete_user(userid: str) -> bool:
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
        _id = (
            _Post.select(fn.MAX(_Post.id) + 1).scalar() or 1
        )  # 获取当前最大 ID 并加 1，初始为 1
        if attpassword:
            attpassword = sha256(attpassword)
        else:
            attpassword = ""
        _Post.create(
            id=_id,
            authorid=authorid,
            title=title,
            created_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            content=content,
            attachments=attachments,
            attpassword=attpassword,
            tags=tags or [],
        )
        return _id

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
    def search(keyword: str) -> list[dict[str, str]]:
        return (
            _Post.select()
            .where((_Post.title.contains(keyword)) | (_Post.content.contains(keyword)))
            .order_by(_Post.id.desc())
            .dicts()
        )

    @staticmethod
    def search_with_paginate(
        keyword: str, page: int, page_size: int
    ) -> list[dict[str, str]]:
        return (
            _Post.select()
            .where((_Post.title.contains(keyword)) | (_Post.content.contains(keyword)))
            .order_by(_Post.id.desc())
            .paginate(page, page_size)
            .dicts()
        )

    @staticmethod
    def search_count(keyword: str) -> int:
        return (
            _Post.select()
            .where((_Post.title.contains(keyword)) | (_Post.content.contains(keyword)))
            .count()
        )

    @staticmethod
    def get_with_paginate(page: int, page_size: int) -> list[dict[str, str]]:
        return (
            _Post.select().order_by(_Post.id.desc()).paginate(page, page_size).dicts()
        )

    @staticmethod
    def get(_id: int) -> dict | None:
        post = _Post.select().where(_Post.id == _id).dicts().get_or_none()
        if not post:
            return None
        comments = []
        valid_ids = []
        for cid in post.get("comments") or []:
            comment = _Comment.select().where(_Comment.id == cid).dicts().get_or_none()
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
        if len(valid_ids) != len(post.get("comments") or []):
            post_obj = _Post.get_or_none(_Post.id == _id)
            if post_obj:
                post_obj.comments = valid_ids
                post_obj.save()
        post["comments"] = comments
        return post

    @staticmethod
    def add_comment(
        postid: int,
        userid: str,
        content: str,
    ) -> int | None:
        """添加评论，返回评论 ID"""
        cid = _Comment.select(fn.MAX(_Comment.id) + 1).scalar() or 1
        _Comment.create(
            id=cid,
            postid=postid,
            userid=userid,
            content=content,
            created_at=datetime.datetime.now().isoformat(),
        )
        post = _Post.get_or_none(_Post.id == postid)
        if post:
            post.comments = (post.comments or []) + [cid]
            post.save()
            return cid
        return None

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
    def get_thumbnail_bytes(saved_name: str, max_width: int = 300) -> bytes:
        file_bytes = Attachment.get_file(saved_name)
        img = Image.open(io.BytesIO(file_bytes))
        if img.mode == "RGBA":
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
        file_path = os.path.join(ATTACHMENTS_DIR, saved_name)  # NOQA
        try:
            with open(file_path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return b""
        except OSError as e:
            return b""

    @staticmethod
    def get_base64(saved_name: str) -> str:
        """
        根据保存的文件名读取附件并转为 base64 字符串。
        用于在页面上展示图片等。
        """
        file_bytes = Attachment.get_file(saved_name)
        return base64.b64encode(file_bytes).decode()


class Avatar:
    @staticmethod
    def save(uploaded_file) -> dict:
        """
        保存上传的头像到本地磁盘，返回文件的元数据。

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

        # 裁剪为 1:1 正方形（取中心区域）
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

        # 保存文件到 avatars 目录
        file_path = os.path.join(AVATARS_DIR, unique_name)  # NOQA
        with open(file_path, "wb") as f:
            f.write(file_bytes)

        # 返回元数据（不包含文件内容，只存路径）
        return {
            "original_name": uploaded_file.name,  # 原始文件名
            "saved_name": unique_name,  # 存储在磁盘的文件名
            "type": uploaded_file.type,  # MIME 类型（如 image/png）
            "size": uploaded_file.size,  # 文件大小（字节）
            "path": file_path,  # 相对路径（用于读取时拼接）
        }
# endregion


class Config:
    def __init__(self):
        self.load()

    def load(self):
        with open("config.toml", "r+", encoding="utf-8") as f:
            self.config = toml.load(f)

    def save(self):
        with open("config.toml", "w+", encoding="utf-8") as f:
            toml.dump(self.config, f)


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
    result = []
    for comment in _Comment.select().dicts():
        post = _Post.get_or_none(_Post.id == comment["postid"])
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
    result = []
    for comment in _Comment.select().dicts():
        if not _Post.get_or_none(_Post.id == comment["postid"]):
            result.append(comment)
    return result


def delete_orphaned_comments() -> int:
    """删除所有孤立评论，返回删除数量"""
    orphans = get_orphaned_comments()
    for c in orphans:
        _Comment.delete().where(_Comment.id == c["id"]).execute()
    return len(orphans)
# endregion