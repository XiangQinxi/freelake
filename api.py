import datetime
import hashlib
import typing

import streamlit as st
from peewee import *
from playhouse.mysql_ext import JSONField  # MySQL 专用

db = SqliteDatabase("data.db")
salt = "freelake"


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

    author = CharField()
    content = TextField()
    created_at = DateTimeField(default=datetime.datetime.now)
    attachments = JSONField(default=list)


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


class Post:
    @staticmethod
    def publish(
        author: str,
        content: str,
        attachments: typing.List[dict[str, str]],
    ):
        """发布文章"""
        _Post.create(author=author, content=content, attachments=attachments)

    @staticmethod
    def get_all() -> list[dict[str, str]]:
        """数据整理成`list[dict[str, str]]`"""
        return list(_Post.select().dicts())