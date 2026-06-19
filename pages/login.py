import time

import streamlit as st

from api import User

st.subheader("登录")

state = st.session_state
cookies = state["cookies"]
user = User()

with st.form("Login"):
    userid = st.text_input("用户ID")
    password = st.text_input("密码", type="password")

    if st.form_submit_button("确认"):
        if not userid:
            st.error("用户ID不能为空")
        elif not password:
            st.error("密码不能为空")
        else:
            if user.login(userid, password):
                st.success("登录成功！")
                secretkey = User.get_secret_key(userid)

                cookies["userid"] = userid
                cookies["password"] = password
                cookies["secretkey"] = secretkey
                state["userid"] = userid
                state["password"] = password
                state["secretkey"] = secretkey
                time.sleep(2)

                st.rerun()
            else:
                st.error("用户ID或密码错误")
