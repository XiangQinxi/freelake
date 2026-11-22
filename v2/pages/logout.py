"""
FreeLake 退出登录（pages/logout.py）
====================================

撤销当前会话 token（服务端标记 revoked），再清空本地状态，然后跳回首页。
Cookie 里只删会话 token；旧版本遗留的 userid/password/secretkey 一并清掉。
"""

import streamlit as st

from api import revoke_session

state = st.session_state
cookies = state["cookies"]

revoke_session(state.get("token", ""))

state["userid"] = ""
state["password"] = ""
state["secretkey"] = ""
state["token"] = ""
state["role"] = ""

for _key in ("session_token", "userid", "password", "secretkey"):
    if _key in cookies:
        del cookies[_key]

st.switch_page("pages/home.py")
