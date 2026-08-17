"""
FreeLake 注册（pages/register.py）
==================================

注册表单：校验密码强度（至少 8 位且包含字母与数字）、两次输入一致、
用户ID不重复后创建账号，并直接以新账号登录（写入 Cookie 与会话状态）。
"""
import streamlit as st

from api import User

st.subheader(":material/person_add: 注册")
st.caption("创建账号，加入 FreeLake 自由论坛。")

state = st.session_state
cookies = state["cookies"]
user = User()

with st.form("Register"):
    userid = st.text_input("用户ID")
    username = st.text_input("用户名")
    password = st.text_input("密码", type="password")
    password2 = st.text_input("确认密码", type="password")

    allowed = True

    if password:
        if len(password) < 8:
            allowed = False
            st.error("密码长度不能小于8位！")

        if not any(char.isalpha() for char in password):
            allowed = False
            st.error("密码必须包含字母和数字的组合！")

    st.caption("密码要求：至少 8 位，且同时包含字母和数字。")

    if st.form_submit_button(":material/person_add: 注册", type="primary"):
        if not userid:
            st.error("用户ID不能为空")
        elif not username:
            st.error("用户名不能为空")
        elif not password:
            st.error("密码不能为空")
        else:
            if password != password2:
                st.error("重新输入的密码与原来的密码不一致")
            elif not allowed:
                pass
            else:
                if user.register(userid, username, password):  # NOQA
                    secretkey = User.get_secret_key(userid)

                    cookies["userid"] = userid
                    cookies["password"] = password
                    cookies["secretkey"] = secretkey
                    state["userid"] = userid
                    state["password"] = password
                    state["secretkey"] = secretkey
                    st.toast("注册成功！")
                    st.rerun()
                else:
                    st.error("用户ID已存在")
