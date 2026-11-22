"""
登录会话（freelake/services/sessions.py）
========================================

Cookie 里只放**随机 token**，库里只存 `sha256(token)`：

- 浏览器被 XSS / 插件读到 Cookie 也拿不到用户密码；
- 服务端可以单独撤销某个会话（退出登录只废当前设备）；
- 换密码时可以把该用户的所有会话一次性废掉。

token 形如 ``sess_<24 字节 urlsafe>``，有效期 ``SESSION_TTL_DAYS`` 天。
"""

import datetime

from freelake.db.models import (
    NOW_FORMAT,
    USER_ACTIVE,
    now_str,
)
from freelake.db.models import (
    SessionModel as _Session,
)
from freelake.db.models import (
    UserModel as _User,
)
from freelake.services.security import generate_sk_key, sha256

SESSION_TTL_DAYS = 14
TOKEN_PREFIX = "sess_"


def _expires_at(days: int) -> str:
    """当前时间 + days 天，格式与库内时间列一致。"""
    return (datetime.datetime.now() + datetime.timedelta(days=days)).strftime(NOW_FORMAT)


def create_session(
    userid: str,
    *,
    ttl_days: int = SESSION_TTL_DAYS,
    user_agent: str = "",
    ip: str = "",
) -> str:
    """为用户新建会话，返回**明文 token**（只返回这一次）。用户不可用时返回空串。"""
    user = _User.get_or_none(_User.userid == userid)
    if user is None or user.status != USER_ACTIVE:
        return ""
    token = TOKEN_PREFIX + generate_sk_key(24)
    _Session.create(
        user_id=userid,
        token_hash=sha256(token),
        created_at=now_str(),
        expires_at=_expires_at(ttl_days),
        revoked_at="",
        user_agent=(user_agent or "")[:200],
        ip=(ip or "")[:64],
    )
    return token


def resolve_session(token: str | None) -> dict[str, str] | None:
    """token → 会话信息；无效/过期/已撤销/用户被禁用时返回 None。"""
    token = (token or "").strip()
    if not token:
        return None
    row = _Session.get_or_none(_Session.token_hash == sha256(token))
    if row is None or row.revoked_at:
        return None
    if row.expires_at and row.expires_at <= now_str():
        return None
    user = _User.get_or_none(_User.userid == row.user_id)
    if user is None or user.status != USER_ACTIVE:
        return None
    return {
        "userid": user.userid,
        "role": user.role,
        "token_hash": row.token_hash,
        "expires_at": row.expires_at,
    }


def revoke_session(token: str | None) -> bool:
    """撤销单个会话（退出登录）。返回是否真的撤销了一个有效会话。"""
    token = (token or "").strip()
    if not token:
        return False
    return (
        _Session.update(revoked_at=now_str())
        .where((_Session.token_hash == sha256(token)) & (_Session.revoked_at == ""))
        .execute()
        > 0
    )


def revoke_user_sessions(userid: str, *, keep_token: str = "") -> int:
    """撤销某用户的全部会话（改密/封禁时用）。keep_token 指定的会话保留。"""
    query = (_Session.user_id == userid) & (_Session.revoked_at == "")
    if keep_token:
        query = query & (_Session.token_hash != sha256(keep_token))
    return _Session.update(revoked_at=now_str()).where(query).execute()


def purge_sessions() -> int:
    """清理已过期的会话行（启动时可调，避免表无限增长）。"""
    return _Session.delete().where(_Session.expires_at < now_str()).execute()


def count_sessions(userid: str) -> int:
    """某用户当前有效会话数（排查「为什么我还登录着」时用）。"""
    return (
        _Session.select()
        .where(
            (_Session.user_id == userid)
            & (_Session.revoked_at == "")
            & (_Session.expires_at >= now_str())
        )
        .count()
    )


__all__ = [
    "SESSION_TTL_DAYS",
    "TOKEN_PREFIX",
    "create_session",
    "resolve_session",
    "revoke_session",
    "revoke_user_sessions",
    "purge_sessions",
    "count_sessions",
]
