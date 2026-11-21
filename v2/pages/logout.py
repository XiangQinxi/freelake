"""
FreeLake 退出登录（pages/logout.py）
====================================

清空会话状态与 Cookie 中的登录凭据（userid/password/secretkey），
然后跳回首页。
"""

import streamlit as st

state = st.session_state
cookies = state["cookies"]

state["userid"] = ""
state["password"] = ""
state["secretkey"] = ""
cookies["userid"] = ""
cookies["password"] = ""
cookies["secretkey"] = ""

st.switch_page("pages/home.py")
