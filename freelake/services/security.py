"""
密码、会话密钥与头像（freelake/services/security.py）
=====================================================

认证相关的密码学细节都收在这里：PBKDF2 哈希、旧 sha256 兼容、「同一会话内
已校验」缓存、管理员会话密钥签发、头像白名单与读写。

注意：`verify_password` 的成功结果会在 Streamlit 会话里记忆 5 分钟，缓存键
带「库里存的哈希」，改密后旧缓存自然失效。
"""

import hashlib
import hmac
import io
import os
import re
import secrets
import time
import uuid

import streamlit as st
from PIL import Image

from freelake.config import (
    AVATAR_QUALITY,
    AVATARS_DIR,
    DIR,
    MAX_AVATAR_DIM,
    PASSWORD_OK_TTL,
    PBKDF2_ITERATIONS,
    salt,
)


def sha256(value):
    """获取哈希加密加盐后的文本（旧版用户密码与附件密码使用）"""
    return hashlib.sha256((value + salt).encode()).hexdigest()


# 用户 ID：3–20 位字母/数字/下划线/连字符（全站用它拼 URL 与 Cookie）


def is_valid_password(password: str | None) -> bool:
    """密码规则：至少 8 位且同时包含字母与数字（与注册页提示语一致）。"""
    if not password or len(password) < 8 or len(password) > 128:
        return False
    return any(ch.isalpha() for ch in password) and any(ch.isdigit() for ch in password)


def hash_password(password: str) -> str:
    """使用 PBKDF2-SHA256 加盐哈希用户密码，返回自包含的存储字符串。"""
    pw_salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), pw_salt.encode(), PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2${PBKDF2_ITERATIONS}${pw_salt}${digest}"


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
        cache[_password_cache_key(password, stored)] = time.time() + PASSWORD_OK_TTL
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


def generate_sk_key(byte_length=40):
    """
    生成以 'sk' 开头的安全随机密钥。
    :param byte_length: 随机部分的字节数（结果长度约为 byte_length * 2 的十六进制形式）
    :return: 形如 'sk-' + 十六进制字符串的密钥
    """
    random_bytes = secrets.token_bytes(byte_length)
    hex_part = random_bytes.hex()  # 每个字节转成两个十六进制字符
    return "sk-" + hex_part


# 模型定义已迁移到 ``freelake.db.models``（v4）；上方按旧名别名导入。


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
