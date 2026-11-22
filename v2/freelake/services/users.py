"""
用户与会话（freelake/services/users.py）
=======================================

注册 / 登录 / 改资料 / 改角色 / 删号 / 签发管理员会话密钥。所有密码校验都走
`security.verify_password`；角色只能经 `set_role` 修改（防自提权）。
"""

import datetime
import typing

from const import admin, user
from freelake.config import SECRET_KEY_TTL_DAYS, VALID_USERID_RE
from freelake.db.adapters import _user_dict
from freelake.db.connection import UNIQUE_VIOLATION, db
from freelake.db.models import (
    NOW_FORMAT,
    USER_ACTIVE,
    now_str,
)
from freelake.db.models import (
    BookmarkModel as _Bookmark,
)
from freelake.db.models import (
    DraftModel as _Draft,
)
from freelake.db.models import (
    DraftTagModel as _DraftTag,
)
from freelake.db.models import (
    LikeModel as _Like,
)
from freelake.db.models import (
    SessionModel as _Session,
)
from freelake.db.models import (
    UserModel as _User,
)
from freelake.services.security import (
    generate_sk_key,
    hash_password,
    is_valid_avatar_name,
    is_valid_password,
    sha256,
    verify_password,
)


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
        if not VALID_USERID_RE.fullmatch(userid):
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
                    password_hash=hash_password(password),
                    role=role,
                    status=USER_ACTIVE,
                    created_at=now_str(),
                    updated_at=now_str(),
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
            if (
                user
                and user.status == USER_ACTIVE
                and verify_password(password, user.password_hash)
            ):
                # 旧版 sha256 密码校验成功后自动升级为 PBKDF2
                if not user.password_hash.startswith("pbkdf2$"):
                    user.password_hash = hash_password(password)
                user.last_login_at = now_str()
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
        if self.check(admin_userid, admin_password) and (
            self.get_config(admin_userid)["role"] == admin
        ):
            return True
        if not secret_key:
            return None
        user = _User.get_or_none(_User.secret_key_hash == sha256(secret_key))
        if user is None or user.role != admin:
            return False
        # 过期时间格式固定，字典序即时间序
        return bool(user.secret_key_expires_at) and (user.secret_key_expires_at >= now_str())

    @staticmethod
    def exists(userid: str) -> bool:
        """检查账号是否存在"""
        return _User.get_or_none(_User.userid == userid)

    @staticmethod
    def get_all() -> list[dict[str, str]]:
        """数据整理成`list[dict[str, str]]`（经适配层，不泄露哈希列）。"""
        return [_user_dict(row) for row in _User.select().dicts()]

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
        if (
            user is None
            or user.status != USER_ACTIVE
            or not verify_password(password, user.password_hash)
        ):
            return None
        key = generate_sk_key(16)
        expires = (datetime.datetime.now() + datetime.timedelta(days=SECRET_KEY_TTL_DAYS)).strftime(
            NOW_FORMAT
        )
        _User.update(secret_key_hash=sha256(key), secret_key_expires_at=expires).where(
            _User.userid == userid
        ).execute()
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
                user.password_hash = hash_password(new_password)
            user.updated_at = now_str()
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
        if not (self.check_admin(admin_secret_key) or self.is_admin(admin_userid, admin_password)):
            return False
        return _User.update(role=role).where(_User.userid == target_userid).execute() > 0

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
                with db.atomic():
                    # 逻辑外键的附属数据要应用层清理（v1 是留孤儿）：
                    # 会话、点赞、收藏、草稿（含草稿标签）。文章不删，
                    # 作者列是逻辑外键，历史内容仍以「已注销」展示。
                    _Session.delete().where(_Session.user_id == userid).execute()
                    _Like.delete().where(_Like.user_id == userid).execute()
                    _Bookmark.delete().where(_Bookmark.user_id == userid).execute()
                    draft_ids = [
                        row["id"]
                        for row in _Draft.select(_Draft.id).where(_Draft.user_id == userid).dicts()
                    ]
                    if draft_ids:
                        _DraftTag.delete().where(_DraftTag.draft.in_(draft_ids)).execute()
                    _Draft.delete().where(_Draft.user_id == userid).execute()
                    user.delete_instance()
                return True
            except Exception:
                return False
