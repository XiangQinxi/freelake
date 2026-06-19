import os

import streamlit as st
from streamlit_cookies_manager import EncryptedCookieManager

from api import User
from api2 import check_by_state
from const import admin

state = st.session_state

if "userid" not in state:
    state["userid"] = ""
if "password" not in state:
    state["password"] = ""

cookies = EncryptedCookieManager(
    prefix="freelake/",
    password=os.environ.get("COOKIES_PASSWORD", "your-strong-secret-password-here"),
)
state["cookies"] = cookies

if not cookies.ready():
    st.info("⏳ 正在加载 Cookies，请稍候...")
    st.stop()

state["userid"] = cookies.get("userid", "")
state["password"] = cookies.get("password", "")

st.set_page_config("FreeLake · 自由论坛", page_icon="logo.ico", layout="centered")

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

user = User()
account_pages = []
if not check_by_state():
    account_pages.append(login_page)
    account_pages.append(register_page)
else:
    account_pages.append(publish_page)
    account_pages.append(userconfig_page)
    if user.get_config(state["userid"]).get("role") == admin:  # NOQA
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

with st.sidebar:
    pass

with st.bottom:
    st.caption("© 2026 XiangQinxi · All rights reserved")
