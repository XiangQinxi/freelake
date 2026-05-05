import time

import streamlit as st

from api import User, sha256

st.subheader("FreeLake ~ 登录", text_alignment="center")

state = st.session_state
user = User()

with st.form("Login&Register"):
    username = st.text_input("用户名")
    password = st.text_input("密码", type="password")

    register = st.toggle("注册")

    if register:
        password2 = st.text_input("请重新输入密码", type="password")

    if st.form_submit_button("确认"):
        if not username:
            st.error("用户名不能为空")
        elif not password:
            st.error("密码不能为空")
        else:
            if register:
                if password != password2:
                    st.error("重新输入的密码与原来的密码不一致")
                else:
                    if user.register(username, password):  # NOQA
                        st.success("注册成功！")

                        state["username"] = username
                        state["password"] = password
                        time.sleep(2)
                        st.switch_page("home.py")
                    else:
                        st.error("用户名已存在")
            else:
                if user.login(username, password):
                    st.success("登录成功！")

                    state["username"] = username
                    state["password"] = password
                    time.sleep(2)
                    st.switch_page("home.py")
                else:
                    st.error("用户名或密码错误")
