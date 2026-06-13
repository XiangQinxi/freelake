import base64
import io
import json
import time
from zipfile import ZipFile

import streamlit as st
from streamlit_extras.pagination import pagination

from api import Attachment, Bookmark, Like, Post, User, format_size, sha256
from const import admin, tags

user = User()
params = st.query_params
state = st.session_state
post_id = params.get("post_id")
user_id = params.get("user_id")  # 用于查看某人的简介
search_keyword = params.get("search_keyword", None)
userconfig = user.get_config(state["userid"])
if userconfig is None:
    userconfig = {
        "userid": None,
        "username": None,
        "created_at": None,
        "description": None,
        "role": None,
        "avatar": None,
    }

if "attpassword" not in state:
    state["attpassword"] = sha256("")


# region 对话框
@st.dialog("确认删除")
def confirm_delete_post(post_id):
    st.warning("确定要删除这篇文章吗？此操作不可撤销！")
    if st.button("确认删除", type="primary"):
        try:
            if Post.delete(post_id):
                st.success("文章删除成功！")
                time.sleep(2)
                st.rerun()
            else:
                st.error("文章不存在或已被删除")
        except Exception as e:
            st.error(f"删除失败：{e}")


@st.dialog("编辑该文章")
def edit_post(post):
    new_title = st.text_input(
        "文章标题",
        placeholder="请输入标题",
        value=post["title"],
        label_visibility="collapsed",
    )
    new_content = st.text_area(
        "文章内容",
        placeholder="请输入内容",
        value=post["content"],
        label_visibility="collapsed",
    )
    new_tags = st.multiselect(
        "标签", tags, default=post["tags"], accept_new_options=False
    )
    if st.button("提交修改"):
        if not new_title:
            st.error("请输入标题！")
        elif not new_content:
            st.error("请输入内容！")
        else:
            if Post.edit(post["id"], new_title, new_content, new_tags):
                st.success("文章修改成功！")
                time.sleep(2)
                st.rerun()
            else:
                st.error("文章修改失败！")


# endregion


# region 常用方法
def basic_information(post):
    col_1, col_2, col_3 = st.columns([0.1, 0.8, 0.1])
    col_1.image(
        user.get_config(post["authorid"])["avatar"],
        width=55,
        link=f"user_config.py?user_id={post['authorid']}",
    )
    col_2.subheader(f"{post['title']}")
    if user.check_by_state():
        if (
            post["authorid"] == state["userid"] or userconfig["role"] == admin
        ):  # 只有作者或管理员才能操作
            action = col_3.menu_button(
                "",
                options=[":material/edit: 编辑", ":material/delete: 删除"],
                icon=":material/more_vert:",
                key=f"{post['id']}.menu",
                type="tertiary",
            )
            match action:
                case ":material/edit: 编辑":
                    edit_post(post)
                case ":material/delete: 删除":
                    confirm_delete_post(post["id"])
    if post["tags"]:
        for tag in post["tags"]:
            st.badge(tag, color="primary")
    st.table(
        {
            ":material/person: 作者名称": user.get_config(post["authorid"])[
                "username"
            ],  # NOQA
            ":material/access_time: 发布时间": post["created_at"],
            ":material/info: 文章ID": post["id"],
        },
        border="horizontal",
        width="content",
    )


def preview(att, container, saved_name, auto_preview=True):
    if att.get("type", "").startswith("image/"):
        if (
            not attpassword
            or not auto_preview
            or state["attpassword"] == attpassword
            or state["userid"] == post.get("authorid")
            or userconfig.get("role") == admin
        ):
            b64 = Attachment.get_thumbnail_base64(saved_name, max_width=200)  # NOQA
            container.image(
                f"data:image/jpeg;base64,{b64}",  # NOQA
                width=60,  # NOQA
            )  # NOQA
        else:
            container.markdown(":material/image:")
    elif att.get("type", "").startswith("video/"):
        container.markdown(":material/smart_display:")
    elif att.get("type", "").startswith("audio/"):
        container.markdown(":material/headphones:")
    elif att.get("type", "").startswith("application/"):
        container.markdown(":material/insert_drive_file:")
    else:
        container.markdown(":material/insert_drive_file:")


def like_and_bookmarked(post):
    with st.container(horizontal=True):
        liked = (
            Like.is_liked(post["id"], state["userid"]) if state.get("userid") else False
        )
        if st.button(
            f"{':material/favorite:' if liked else ':material/favorite_border:'} {Like.count(post['id'])}",
            key=f"detail_like_{post['id']}",
            type="tertiary",
        ):
            if state.get("userid"):
                Like.toggle(post["id"], state["userid"])
                st.rerun()
            else:
                st.warning("请先登录")
        bookmarked = (
            Bookmark.is_bookmarked(post["id"], state["userid"])
            if state.get("userid")
            else False
        )
        if st.button(
            ":material/bookmark:" if bookmarked else ":material/bookmark_border:",
            key=f"detail_bm_{post['id']}",
            type="tertiary",
        ):
            if state.get("userid"):
                Bookmark.toggle(post["id"], state["userid"])
                st.rerun()
            else:
                st.warning("请先登录")


# endregion


# region 用户信息查询
if user_id:
    if st.button("返回主页", type="primary"):
        del st.query_params["user_id"]
        st.rerun()
    userconfig = user.get_config(user_id)
    if userconfig:
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
    else:
        st.error("该用户不存在！")
# endregion
else:
    # region 文章信息查询
    if post_id:
        if st.button("返回主页", type="primary"):
            del st.query_params["post_id"]
            st.rerun()
        post = Post.get(int(post_id))
        if post:
            basic_information(post)

            st.markdown(post["content"])

            # like_and_bookmarked(post)

            # 显示附件
            attachments = post.get("attachments", [])
            if attachments:
                st.divider()

                attpassword = post.get("attpassword")

                if (
                    (attpassword and state["attpassword"] != attpassword)
                    and state["userid"] != post["authorid"]
                    and userconfig["role"] != admin
                ):
                    attpwd = st.text_input(
                        "专属密码",
                        placeholder="请输入专属密码以查看附件内容！",
                        type="password",
                    )
                    if st.button("检查", type="primary"):
                        state["attpassword"] = sha256(attpwd)
                        if sha256(attpwd) == attpassword:
                            st.success("验证成功！")
                            time.sleep(1)
                            st.rerun()
                else:
                    for att in attachments:
                        saved_name = att.get("saved_name", "")
                        file_bytes = (
                            Attachment.get_file(saved_name) if saved_name else b""
                        )
                        if not file_bytes and saved_name:
                            st.warning(
                                f"附件 {att.get('original_name', '未命名')} 文件已丢失"
                            )
                            continue
                        b64 = base64.b64encode(file_bytes).decode()
                        if att.get("type", "").startswith("image/"):
                            st.image(f"data:{att['type']};base64,{b64}")  # NOQA
                    st.divider()
                    st.caption("📎 附件")

                    zip_buffer = io.BytesIO()
                    missing_count = 0
                    with ZipFile(zip_buffer, "w") as zf:
                        for att in attachments:
                            saved_name = att.get("saved_name", "")
                            file_bytes = (
                                Attachment.get_file(saved_name) if saved_name else b""
                            )
                            if file_bytes:
                                zf.writestr(
                                    att.get("original_name", "download"), file_bytes
                                )
                            else:
                                missing_count += 1
                    if missing_count:
                        st.warning(f"有 {missing_count} 个附件文件已丢失，无法打包")
                    st.download_button(
                        label=":material/folder_zip: 下载所有附件 (ZIP)",
                        data=zip_buffer.getvalue(),
                        file_name=f"{post['title']}-{post['authorid']}.zip",
                        mime="application/zip",
                        key=f"dl_all_{post['id']}",
                        type="primary",
                    )

                    for att in attachments:
                        col_a, col_b, col_c, col_d = st.columns(
                            [0.1, 0.5, 0.2, 0.2], vertical_alignment="center"
                        )
                        saved_name = att.get("saved_name", "")
                        file_bytes = (
                            Attachment.get_file(saved_name) if saved_name else b""
                        )

                        preview(att, col_a, saved_name, auto_preview=False)

                        col_b.write(f"**{att.get('original_name', '未命名')}**")
                        col_c.write(f"{format_size(att.get('size', 0))}")
                        if not file_bytes and saved_name:
                            col_c.write(":material/warning: 文件已丢失")
                        with col_d.popover("..."):
                            if file_bytes:
                                if st.toggle(
                                    f"预览 “{att.get('original_name', '文件')}”",
                                    key=f"preview_{post['id']}_{saved_name}",
                                ):  # NOQA
                                    b64 = base64.b64encode(file_bytes).decode()
                                    if att.get("type", "").startswith("image/"):
                                        st.image(
                                            f"data:{att['type']};base64,{b64}"
                                        )  # NOQA
                                    elif att.get("type", "").startswith("video/"):
                                        st.video(
                                            f"data:{att['type']};base64,{b64}"
                                        )  # NOQA
                                    elif att.get("type", "").startswith("audio/"):
                                        st.audio(
                                            f"data:{att['type']};base64,{b64}"
                                        )  # NOQA

                                st.download_button(
                                    label=f"下载 “{att.get('original_name', '文件')}”",
                                    data=file_bytes,
                                    file_name=att.get("original_name", "download"),
                                    mime=att.get("type") or "application/octet-stream",
                                    key=f"dl_{post['id']}_{saved_name}",
                                    type="primary",
                                )

            st.divider()
            col_3, col_4 = st.columns([0.8, 0.2])
            new_comment = col_3.text_area(
                "评论",
                placeholder="良言一句三冬暖，恶语伤人六月寒",
                label_visibility="collapsed",
            )
            if col_4.button("提交评论"):
                if not state.get("userid"):
                    st.warning("请先登录以提交评论！")
                else:
                    if new_comment:
                        Post.add_comment(int(post_id), state.get("userid"), new_comment)
                        st.success("评论提交成功")
                        time.sleep(2)
                        st.rerun()
                    else:
                        st.warning("请输入评论内容！")

            # 评论展示
            if post.get("comments"):
                comments = post["comments"]
                with st.expander(
                    f":material/comment: 评论 ({len(comments)})", expanded=True
                ):
                    for index, comment in enumerate(comments):
                        comment = json.loads(comment)
                        cfg = user.get_config(comment["userid"])
                        if cfg:
                            col_a, col_b, col_c = st.columns(
                                [0.08, 0.82, 0.1], vertical_alignment="top"
                            )
                            col_a.image(
                                cfg["avatar"],
                                width=40,
                                link=f"user_config.py?user_id={comment['userid']}",
                            )
                            col_b.markdown(f"**{cfg['username']}**")
                            col_b.caption(comment.get("created_at", ""))
                            col_b.markdown(comment["content"])
                            if user.check_by_state():
                                if (
                                    userconfig.get("userid") == comment["userid"]
                                    or userconfig.get("role") == admin
                                ):
                                    action = col_c.menu_button(
                                        "",
                                        options=[
                                            ":material/edit: 编辑",
                                            ":material/delete: 删除",
                                        ],
                                        icon=":material/more_vert:",
                                        key=f"comment{index}.menu",
                                        type="tertiary",
                                    )
                                    match action:
                                        case ":material/edit: 编辑":
                                            pass
                                        case ":material/delete: 删除":
                                            if Post.delete_comment(int(post_id), index):
                                                st.success("评论删除成功！")
                                                time.sleep(2)
                                                st.rerun()
                                            else:
                                                st.error("评论删除失败！")
        else:
            st.warning("文章不存在或已被删除！")
    # endregion
    # region 主页显示
    else:
        st.text("我构建的简易论坛程序....")

        # region 快捷入口
        if user.check_by_state():
            with st.container(horizontal=True, border=True):
                st.page_link(
                    "publish.py",
                    label="发布文章",
                )
                st.page_link(
                    "user_config.py",
                    label="用户配置",
                )
                if user.get_config(state["userid"])["role"] == admin:
                    st.page_link(
                        "admin.py",
                        label="管理员页面",
                    )
        else:
            st.warning("请登录以解锁更多功能！")
        # endregion

        # region 筛选
        with st.container(border=True):
            search_keyword = st.text_input(
                " ",
                placeholder="搜索",
                label_visibility="collapsed",
                icon=":material/search:",
                value=params.get("search_keyword", ""),
            )
            if search_keyword != params.get("search_keyword", ""):
                if search_keyword:
                    params["search_keyword"] = search_keyword
                else:
                    del params["search_keyword"]
                st.rerun()

            selected_tag = st.pills(":material/filter_alt: 筛选", tags)

            show_bookmarked = (
                st.checkbox(
                    ":material/bookmark: 仅显示已收藏",
                    value=False,
                    disabled=not user.check_by_state(),
                )
                if user.check_by_state()
                else False
            )
        # endregion

        # region 分页
        if search_keyword:
            total = Post.search_count(search_keyword)
        else:
            total = Post.count()

        with st.bottom:
            page = pagination(
                num_pages=max(total // 5 + 1, 1),
                max_visible_pages=5,
                key="interactive_pagination",
            )
        # endregion

        # 显示文章列表
        if total == 0:
            st.info("没有找到相关文章。")
        else:
            if search_keyword:
                posts = Post.search_with_paginate(search_keyword, page, 5)
            else:
                posts = Post.get_with_paginate(page, 5)

            for post in posts:
                # 筛选
                if selected_tag:
                    if selected_tag not in post["tags"]:
                        continue
                post = Post.get(post["id"])
                if post is None:
                    continue
                if show_bookmarked and state.get("userid"):
                    if post["id"] not in Bookmark.get_bookmarked_post_ids(
                        state["userid"]
                    ):
                        continue

                with st.container(border=True):
                    basic_information(post)
                    st.markdown(post["content"][0:40] + "...")

                    # like_and_bookmarked(post)

                    if st.button("查看详细内容", key=f"view_{post['id']}"):
                        params["post_id"] = str(post["id"])
                        st.rerun()
                    # st.info(post)
                    if post.get("comments"):
                        with st.expander("最新一条评论", expanded=True):
                            last_comment = json.loads(post.get("comments")[-1])
                            last_comment_user = user.get_config(last_comment["userid"])
                            st.markdown(
                                f"{last_comment_user['username']}： {last_comment['content']} （{last_comment['created_at']}）"
                            )
                    attachments = post.get("attachments", [])
                    if attachments:
                        st.divider()
                        st.caption(":material/attach_file: 附件")
                        attpassword = post.get("attpassword")

                        for att in attachments:
                            col_a, col_b, col_c = st.columns([0.1, 0.6, 0.3])
                            saved_name = att.get("saved_name", "")
                            file_bytes = (
                                Attachment.get_file(saved_name) if saved_name else b""
                            )

                            preview(att, col_a, saved_name)

                            col_b.write(f"**{att.get('original_name', '未命名')}**")
                            col_c.write(f"{format_size(att.get('size', 0))}")
    # endregion
