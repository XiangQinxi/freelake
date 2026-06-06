import time

import streamlit as st

from api import User, Avatar

user = User()
userconfig = user.get_config(st.session_state.get("userid"))
if userconfig:
    with st.expander("当前用户信息", expanded=True):
        print(userconfig)
        st.image(userconfig["avatar"], width=100)
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
            new_username = st.text_input(
                "名称", placeholder="请输入用户名称！", value=userconfig["username"]
            )
            new_description = st.text_area(
                "自我介绍",
                placeholder="请输入自我介绍！",
                value=userconfig["description"],
            )
            if st.button("提交"):
                if not new_username or not new_description:
                    st.error("请输入名称和自我介绍！")
                else:
                    if new_avatar:
                        meta = Avatar.save(new_avatar)
                    else:
                        meta = {}
                    user.modify_config(
                        st.session_state.get("userid"), new_username, new_description, meta.get("path") if new_avatar else None
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
                    user.modify_password(
                        st.session_state.get("userid"), original_password, new_password
                    )
                    st.success("密码修改成功！")
                    st.rerun()
        if st.button("退出登录", type="primary"):
            st.switch_page("logout.py")
else:
    st.error("用户信息加载失败，请重新登录！")
    time.sleep(2)
    st.switch_page("login.py")
