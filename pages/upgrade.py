import time

import streamlit as st

from api import User
from const import admin

st.subheader("升级管理员")

user = User()
if user.check_by_state():
    secret_key = st.text_input(
        "密钥", type="password", placeholder="请输入密钥", label_visibility="collapsed"
    )
    if secret_key:
        if st.button("升级", type="primary"):
            if user.modify_role(st.session_state["userid"], secret_key, admin):
                st.success("升级成功！")
                time.sleep(2)
                st.switch_page("home.py")
            else:
                st.error("升级失败！")
