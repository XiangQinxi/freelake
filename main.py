import os

import streamlit as st
from streamlit_cookies_manager import EncryptedCookieManager

from api import User
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

st.set_page_config(page_icon="logo.ico", layout="centered")

home_page = st.Page("home.py", title="首页")
publish_page = st.Page("publish.py", title="发布文章")
login_page = st.Page("login.py", title="登录")
logout_page = st.Page("logout.py", title="退出登录")
userconfig_page = st.Page("user_config.py", title="用户配置")
upgrade_page = st.Page("upgrade.py", title="升级管理员")
admin_page = st.Page("admin.py", title="管理员页面")

user = User()
account_pages = []
if not user.check_by_state():
    account_pages.append(login_page)
else:
    account_pages.append(publish_page)
    account_pages.append(userconfig_page)
    if user.get_config(state["userid"]).get("role") != admin:  # NOQA
        account_pages.append(upgrade_page)
    else:
        account_pages.append(admin_page)
    account_pages.append(logout_page)


st.title("FreeLake")


nav = st.navigation({"主页": [home_page], "账号": account_pages}, position="top")
nav.run()

with st.sidebar:
    pass

with st.bottom:
    st.caption("© 2026 XiangQinxi · All rights reserved")
