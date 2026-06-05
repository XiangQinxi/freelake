import time

import streamlit as st

from api import User

user = User()
userconfig = user.get_config(st.session_state.get("username"))
if userconfig:
    with st.expander("当前用户信息", expanded=True):
        st.table(
            {
                ":material/person: 名称": userconfig.get("username"),
                ":material/access_time: 注册时间": userconfig.get("created_at"),
                ":material/info: 自我介绍": userconfig.get("description"),
                ":material/info: 职位": userconfig.get("role"),
            },
            border="horizontal",
            width="content",
        )

    if st.popover("修改密码", type="secondary"):
        pass
    if st.button("退出登录", type="primary"):
        st.switch_page("logout.py")
else:
    st.error("用户信息加载失败，请重新登录！")
    time.sleep(2)
    st.switch_page("login.py")
