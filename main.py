import streamlit as st

from api import User

state = st.session_state

if "username" not in state:
    state["username"] = ""
if "password" not in state:
    state["password"] = ""

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

col1, col2, col3 = st.columns([0.3, 0.1, 0.6], vertical_alignment="bottom")

col1.space()
col2.image("logo.ico")
col3.title("FreeLake")


nav = st.navigation({"主页": [home_page], "账号": account_pages}, position="top")
nav.run()

with st.sidebar:
    pass
