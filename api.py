import base64
import datetime
import hashlib
import os
import typing
import uuid

import streamlit as st
from peewee import *
from playhouse.mysql_ext import JSONField  # NOQA

db = SqliteDatabase("data.db")
salt = "freelake"

# 附件存储目录（在项目根目录下创建 attachments 文件夹）
ATTACHMENTS_DIR = os.path.join(os.path.dirname(__file__), "attachments")  # NOQA
os.makedirs(ATTACHMENTS_DIR, exist_ok=True)


class BaseModel(Model):
    class Meta:
        database = db


class _User(BaseModel):
    """用于存储用户的表"""

    username = CharField()  # 即用户标识
    password = CharField(max_length=256)  # 哈希加密过的密码
    created_at = DateTimeField(default=datetime.datetime.now)
    description = TextField(default="这个用户有点懒，什么也没留下~")
    role = CharField(default="user")

    def __str__(self):
        return self.username


class _Post(BaseModel):
    """用于存储文章的表"""

    id = IntegerField()
    author = CharField()
    title = TextField()
    content = TextField()
    created_at = DateTimeField(default=datetime.datetime.now)
    attachments = JSONField(default=list)
    tags = JSONField(default=list)


db.connect()
db.create_tables([_User, _Post], safe=True)


def sha256(value):
    """获取哈希加密加盐后的文本"""
    return hashlib.sha256((value + salt).encode()).hexdigest()


class User:
    def register(
        self,
        username: str,
        password: str,
        role: typing.Literal["user", "admin"] = "user",
    ) -> bool:
        """注册账号，如果成功则返回`True`"""
        if not self.exists(username):  # 避免重复用户名
            _User.create(username=username, password=sha256(password), role=role)
            return True
        else:
            return False

    def login(self, username: str, password: str) -> bool:
        """登录账号，如果用户名与其密码对应上则返回`True`"""
        if self.exists(username):
            user = _User.get(_User.username == username)
            return user.password == sha256(password)
        return False

    check = login

    @staticmethod
    def exists(username: str) -> bool:
        """检查账号是否存在"""
        return _User.get_or_none(_User.username == username)

    @staticmethod
    def get_all() -> list[dict[str, str]]:
        """数据整理成`list[dict[str, str]]`"""
        return list(_User.select().dicts())

    @staticmethod
    def get_config(username: str) -> dict[str, str | datetime.datetime] | None:
        """获取用户配置"""
        user = _User.get_or_none(_User.username == username)
        if user:
            return {
                "username": user.username,
                "created_at": user.created_at,
                "description": user.description,
                "role": user.role,
            }
        return None

    def modify_password(self, username: str, password: str, new_password: str) -> bool:
        """修改密码"""
        if self.login(username, password):
            user = _User.get(_User.username == username)
            user.password = sha256(new_password)
            user.save()
            return True
        return False

    def check_by_state(self) -> bool:
        """检查当前登录状态通过`streamlit.session_state`"""
        return self.check(
            st.session_state.get("username"), st.session_state.get("password")
        )


def save_attachment(uploaded_file) -> dict:
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


def get_attachment_file(saved_name: str) -> bytes:
    """
    根据保存的文件名读取附件二进制数据。

    参数:
        saved_name: 数据库里存的文件名

    返回:
        bytes: 文件的二进制数据
    """
    file_path = os.path.join(ATTACHMENTS_DIR, saved_name)  # NOQA
    with open(file_path, "rb") as f:
        return f.read()


def get_attachment_base64(saved_name: str) -> str:
    """
    根据保存的文件名读取附件并转为 base64 字符串。
    用于在页面上展示图片等。
    """
    file_bytes = get_attachment_file(saved_name)
    return base64.b64encode(file_bytes).decode()


class Post:
    @staticmethod
    def publish(
        author: str,
        title: str,
        content: str,
        attachments: typing.List[dict[str, str]],
        tags: typing.List[str] = None,
    ):
        """发布文章"""
        print(f"{author}发布了新文章：{title}")
        _id = (
            _Post.select(fn.MAX(_Post.id) + 1).scalar() or 1
        )  # 获取当前最大 ID 并加 1，初始为 1
        _Post.create(
            id=_id, author=author, title=title, content=content, attachments=attachments, tags=tags or []
        )

    @staticmethod
    def get_all() -> list[dict[str, str]]:
        """数据整理成`list[dict[str, str]]`"""
        return list(_Post.select().dicts())

    @staticmethod
    def get(_id: int) -> dict:
        return _Post.select(_Post.id == _id).dicts() 


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
