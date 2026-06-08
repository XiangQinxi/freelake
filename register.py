import time

import streamlit as st

from api import User

st.subheader("注册")

state = st.session_state
cookies = state["cookies"]
user = User()

with st.form("Register"):
    userid = st.text_input("用户ID")
    username = st.text_input("用户名")
    password = st.text_input("密码", type="password")
    password2 = st.text_input("请重新输入密码", type="password")

    if st.form_submit_button("确认"):
        if not userid:
            st.error("用户ID不能为空")
        elif not username:
            st.error("用户名不能为空")
        elif not password:
            st.error("密码不能为空")
        else:
            if password != password2:
                st.error("重新输入的密码与原来的密码不一致")
            else:
                if user.register(userid, username, password):  # NOQA
                    st.success("注册成功！")

                    cookies["userid"] = userid
                    cookies["password"] = password
                    state["userid"] = userid
                    state["password"] = password
                    time.sleep(2)

                    st.rerun()
                else:
                    st.error("用户ID已存在")
