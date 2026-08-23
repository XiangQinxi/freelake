"""
FreeLake 用户配置（pages/user_config.py）
=========================================

个人资料管理页面：查看当前用户信息、修改昵称/自我介绍/头像、修改密码、
退出登录。修改密码成功后会把新密码同步回会话与 Cookie，避免被登出。
"""

import streamlit as st

from api import User, get_avatar_bytes, save_avatar
from const import admin

st.page_link("pages/home.py", label=":material/arrow_back: 返回主页")

st.subheader(":material/manage_accounts: 用户信息")

user = User()
userconfig = user.get_config(st.session_state.get("userid"))
state = st.session_state

if userconfig:
    with st.container(border=True):
        st.image(get_avatar_bytes(userconfig["avatar"]), width=250)
        role_name = "管理员" if userconfig.get("role") == admin else "普通用户"
        st.table(
            {
                ":material/key: 用户ID": userconfig.get("userid"),
                ":material/person: 名称": userconfig.get("username"),
                ":material/access_time: 注册时间": userconfig.get("created_at"),
                ":material/info: 自我介绍": userconfig.get("description")
                or "暂无自我介绍",
                ":material/shield: 职位": role_name,
            },
            border="horizontal",
            width="content",
        )

    with st.container(border=True):

        @st.dialog("编辑资料")
        def modify_config():
            new_avatar = st.file_uploader(
                "上传新头像", type="image/*", label_visibility="collapsed"
            )
            if new_avatar:
                st.image(new_avatar, width=250)
            new_username = st.text_input(
                "用户名",
                placeholder="请输入用户名",
                value=userconfig.get("username"),  # NOQA
            )
            new_description = st.text_area(
                "自我介绍",
                placeholder="请输入自我介绍",
                value=userconfig.get("description"),  # NOQA
            )
            if st.button("保存修改"):
                if not new_username or not new_description:
                    st.error("请输入用户名和自我介绍！")
                else:
                    if new_avatar:
                        meta = save_avatar(new_avatar)
                    else:
                        meta = {}
                    ok = user.modify(
                        state.get("userid"),
                        state.get("password"),
                        username=new_username,
                        description=new_description,
                        avatar=meta.get("path") if new_avatar else None,
                    )
                    if ok:
                        st.toast("用户信息修改成功！")
                        st.rerun()
                    else:
                        st.error("修改失败：请检查密码或登录状态已过期")

        if st.button(":material/edit: 编辑资料"):
            modify_config()
        with st.popover(":material/password: 修改密码", type="secondary"):
            original_password = st.text_input(
                "原密码", placeholder="请输入原密码", type="password"
            )
            new_password = st.text_input(
                "新密码", placeholder="请输入新密码", type="password"
            )
            if st.button("提交"):
                if not original_password or not new_password:
                    st.error("请输入原密码和新密码！")
                else:
                    ok = user.modify(
                        state.get("userid"),
                        password=original_password,
                        new_password=new_password,
                    )
                    if ok:
                        # 同步更新会话与 cookie 中的密码，避免改密后被登出
                        state["password"] = new_password
                        state["cookies"]["password"] = new_password
                        st.toast("密码修改成功！")
                        st.rerun()
                    else:
                        st.error("原密码错误，修改失败！")
        if st.button(":material/logout: 退出登录", type="primary"):
            st.switch_page("logout.py")
else:
    st.error("用户信息加载失败，请重新登录！")
    st.toast("请重新登录")
    st.switch_page("login.py")
