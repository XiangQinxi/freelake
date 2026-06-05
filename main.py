import streamlit as st
import os

from api import User
from streamlit_cookies_manager import EncryptedCookieManager

state = st.session_state

if "username" not in state:
    state["username"] = ""
if "password" not in state:
    state["password"] = ""
if "cookies" not in state:
    state["cookies"] = EncryptedCookieManager(
        # 前缀 - 防止在 Streamlit Cloud 等多应用环境下 cookie 名称冲突
        prefix="freelake/",

        # 加密密码 - 生产环境务必从 secrets 或环境变量读取
        password=os.environ.get("COOKIES_PASSWORD", "your-strong-secret-password-here"),
    )

    if not state["cookies"].ready():
        st.info("⏳ 正在加载 Cookies，请稍候...")
        st.stop()  # 未就绪时停止执行，避免读取到空值

    state["username"] = state["cookies"].get("username", "")
    state["password"] = state["cookies"].get("password", "")

st.set_page_config(page_icon="logo.ico", layout="centered")

home_page = st.Page("home.py", title="首页")
login_page = st.Page("login.py", title="登录")
logout_page = st.Page("logout.py", title="退出登录")

user = User()
account_pages = []
if not user.check_by_state():
    account_pages.append(login_page)
else:
    account_pages.append(logout_page)

st.title("FreeLake")
st.text("我构建的简易论坛程序....")


nav = st.navigation({"主页": [home_page], "账号": account_pages}, position="top")
nav.run()

with st.sidebar:
    pass
