import time

import streamlit as st

from api import save_avatar, User

st.page_link("pages/home.py", label="返回主页")

user = User()
userconfig = user.get_config(st.session_state.get("userid"))
state = st.session_state

if userconfig:
    with st.expander("当前用户信息", expanded=True):
        st.image(userconfig["avatar"], width=250)
        st.table(
            {
                ":material/key: 用户ID": userconfig.get("userid"),
                ":material/person: 名称": userconfig.get("username"),
                ":material/access_time: 注册时间": userconfig.get("created_at"),
                ":material/info: 自我介绍": userconfig.get("description"),
                ":material/info: 职位": userconfig.get("role"),
            },
            border="horizontal",
            width="content",
        )

    with st.container(border=True):

        @st.dialog("修改用户信息")
        def modify_config():
            new_avatar = st.file_uploader(
                "上传新头像", type="image/*", label_visibility="collapsed"
            )
            if new_avatar:
                st.image(new_avatar, width=250)
            new_username = st.text_input(
                "名称",
                placeholder="请输入用户名称！",
                value=userconfig.get("username"),  # NOQA
            )
            new_description = st.text_area(
                "自我介绍",
                placeholder="请输入自我介绍！",
                value=userconfig.get("description"),  # NOQA
            )
            if st.button("提交"):
                if not new_username or not new_description:
                    st.error("请输入名称和自我介绍！")
                else:
                    if new_avatar:
                        meta = save_avatar(new_avatar)
                    else:
                        meta = {}
                    user.modify(
                        state.get("userid"),
                        state.get("password"),
                        username=new_username,
                        description=new_description,
                        avatar=meta.get("path") if new_avatar else None,
                    )
                    st.success("用户信息修改成功！")
                    time.sleep(2)
                    st.rerun()

        if st.button("修改用户信息"):
            modify_config()
        with st.popover("修改密码", type="secondary"):
            original_password = st.text_input("请输入原密码", type="password")
            new_password = st.text_input("请输入新密码", type="password")
            if st.button("提交"):
                if not original_password or not new_password:
                    st.error("请输入原密码和新密码！")
                else:
                    user.modify(
                        state.get("userid"),
                        password=original_password,
                        new_password=new_password,
                    )
                    st.success("密码修改成功！")
                    st.rerun()
        if st.button("退出登录", type="primary"):
            st.switch_page("logout.py")
else:
    st.error("用户信息加载失败，请重新登录！")
    time.sleep(2)
    st.switch_page("login.py")
