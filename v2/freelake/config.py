"""
FreeLake 运行期配置（freelake/config.py）
=========================================

集中放路径、上限、敏感列名单等常量：数据层各模块与页面都从这里取，
避免「同一个常量散落在 api.py 与各个 service 里」。
"""

import os
import re

# 旧版 sha256 哈希用的静态盐（仅用于兼容历史密码与附件密码，勿新增用途）
salt = "freelake"

# 附件 / 头像目录（相对代码目录，便于整仓迁移）
DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATTACHMENTS_DIR = os.path.join(DIR, "attachments")
AVATARS_DIR = os.path.join(DIR, "avatars")

# 头像压缩参数：边长上限与 JPEG 质量（上传的原始高清图会被缩放重编码）
MAX_AVATAR_DIM = 512
AVATAR_QUALITY = 85
# 单个附件大小上限（与 .streamlit/config.toml 的 server.maxUploadSize 一致）
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
# 落盘扩展名只允许字母数字，杜绝 .php/.html 之类可执行/可渲染后缀
ATTACHMENT_EXT_RE = re.compile(r"^\.[A-Za-z0-9]{1,10}$")

# 管理后台「只读 SQL」结果里必须打码的列
SENSITIVE_COLUMNS = frozenset(
    {"password", "secret_key", "secret_key_hash", "secret_key_expires_at", "attpassword"}
)

# 密码 / 会话密钥参数
PBKDF2_ITERATIONS = 200_000
PASSWORD_OK_TTL = 300  # 秒：同一会话内「密码校验通过」的记忆有效期
SECRET_KEY_TTL_DAYS = 30  # 管理员会话密钥有效期

# 公告帖固定标题（启动引导靠它做幂等判断，改标题等于「重新发一次公告」）
ANNOUNCEMENT_POST_TITLE = "欢迎来到 FreeLake · 社区规范"

# 用户 ID：3–20 位字母/数字/下划线/连字符（全站用它拼 URL 与 Cookie）
VALID_USERID_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")
