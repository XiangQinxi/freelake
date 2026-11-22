"""
FreeLake 登录（pages/login.py）
==============================

登录表单：校验用户ID与密码（api.User.login），成功后把凭据写入加密
Cookie 与会话状态，并跳转/刷新。注意：登录后的 rerun 依赖 Cookie 管理
组件的回写，因此表单提交后立即 rerun 即可（组件队列会保证新值在下一次
执行时可见）。
"""

import streamlit as st

from api import User, create_session

st.subheader(":material/login: 登录")
st.caption("使用用户 ID 与密码登录，即可发布文章、参与评论。")

state = st.session_state
cookies = state["cookies"]
user = User()

with st.form("Login"):
    userid = st.text_input("用户ID")
    password = st.text_input("密码", type="password")

    if st.form_submit_button(":material/login: 登录", type="primary"):
        if not userid:
            st.error("用户ID不能为空")
        elif not password:
            st.error("密码不能为空")
        else:
            if user.login(userid, password):
                secretkey = User.issue_secret_key(userid, password)
                token = create_session(userid)

                # Cookie 只放会话 token：密码/管理员密钥一概不落浏览器。
                if token:
                    cookies["session_token"] = token
                    state["token"] = token
                for legacy_key in ("userid", "password", "secretkey"):
                    if legacy_key in cookies:
                        del cookies[legacy_key]
                state["userid"] = userid
                state["role"] = User().get_config(userid).get("role", "")
                # 仅本次会话（内存）内保留，供「改密 / 管理员敏感操作」即时使用；
                # 刷新页面后需要重新输入密码，这是刻意设计。
                state["password"] = password
                state["secretkey"] = secretkey
                st.toast("登录成功！")
                st.rerun()
            else:
                st.error("用户ID或密码错误")
