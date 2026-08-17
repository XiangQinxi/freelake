"""
FreeLake 入口（main.py）
========================

Streamlit 论坛应用的主入口脚本，负责：

1. 初始化会话状态（userid/password）与加密 Cookie 管理器；
2. 根据登录状态构建顶部导航（st.navigation）；
3. 按角色（普通用户 / 管理员）展示不同的「账号」页面组。

运行方式：``streamlit run main.py``

安全约定：
- Cookie 加密密钥优先从环境变量 ``COOKIES_PASSWORD`` 或
  ``.streamlit/secrets.toml`` 的 ``[cookies]`` 段读取（不要使用默认值）；
- 登录状态以「userid + 明文密码」形式保存在加密 Cookie 中，由
  ``api2.check_by_state`` 每次直接查库校验。
"""
import os

import streamlit as st
from streamlit_cookies_manager import EncryptedCookieManager

from api import User
from api2 import check_by_state
from const import admin


def _secrets_get(key, default=None):
    """安全读取 st.secrets，secrets 文件缺失时返回默认值"""
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


# ---- 会话状态初始化 ----
state = st.session_state

if "userid" not in state:
    state["userid"] = ""
if "password" not in state:
    state["password"] = ""

# ---- 加密 Cookie：持久化登录状态（浏览器端加密存储） ----
cookies = EncryptedCookieManager(
    prefix="freelake/",
    password=os.environ.get("COOKIES_PASSWORD")
    or _secrets_get("cookies", {}).get("password")
    or "dev-insecure-fallback-change-me",
)
state["cookies"] = cookies

# Cookie 组件首轮渲染需要浏览器回传，未就绪时先提示并停止本次执行
if not cookies.ready():
    st.info("⏳ 正在加载 Cookies，请稍候...")
    st.stop()

# 从 Cookie 恢复登录状态（未登录时为空字符串）
state["userid"] = cookies.get("userid", "")
state["password"] = cookies.get("password", "")

st.set_page_config("FreeLake · 自由论坛", page_icon="logo.ico", layout="centered")

# ---- 页面注册（st.navigation 多页应用） ----
home_page = st.Page("pages/home.py", title=":material/home: 首页")
publish_page = st.Page("pages/publish.py", title=":material/publish: 发布文章")
login_page = st.Page("pages/login.py", title=":material/login: 登录")
register_page = st.Page("pages/register.py", title=":material/person: 注册")
logout_page = st.Page("pages/logout.py", title=":material/logout: 退出登录")
userconfig_page = st.Page("pages/user_config.py", title=":material/settings: 用户配置")
admin_page = st.Page("pages/admin.py", title=":material/security: 管理员页面")
image_generate_page = st.Page(
    "pages/image_generate.py", title=":material/auto_awesome: 图片生成"
)

# ---- 按登录状态与角色组装「账号」导航组 ----
user = User()
account_pages = []
if not check_by_state():
    # 未登录：只显示 登录 / 注册
    account_pages.append(login_page)
    account_pages.append(register_page)
else:
    # 已登录：发布文章 / 用户配置 / 退出登录；管理员额外获得管理页面与图片生成
    account_pages.append(publish_page)
    account_pages.append(userconfig_page)
    userconfig = user.get_config(state["userid"]) or {}
    if userconfig.get("role") == admin:  # NOQA
        account_pages.append(admin_page)
        account_pages.append(image_generate_page)
    account_pages.append(logout_page)


st.title("FreeLake")


nav = st.navigation(
    {
        ":material/home: 首页": [home_page],
        ":material/manage_accounts: 账号": account_pages,
    },
    position="top",
)
nav.run()

with st.bottom:
    st.caption("© 2026 XiangQinxi · FreeLake 自由论坛")
