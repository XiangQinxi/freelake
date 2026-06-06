import base64
import time

import streamlit as st

from api import Post, User, format_size, Attachment
from const import tags

user = User()
params = st.query_params
state = st.session_state
post_id = params.get("post_id")

if post_id:
    if st.button("返回主页", type="primary"):
        del st.query_params["post_id"]
        st.rerun()
    post = Post.get(int(post_id))
    if post:
        col_1, col_2 = st.columns([0.1, 0.9])
        col_1.image(user.get_config(post["authorid"])["avatar"], width=55)
        col_2.subheader(f"{post['title']}")
        st.table(
            {
                ":material/person: 作者名称": user.get_config(post["authorid"])[
                    "username"
                ],  # NOQA
                ":material/person: 作者ID": post["authorid"],
                ":material/access_time: 发布时间": post["created_at"],
                ":material/info: 文章ID": post["id"],
            },
            border="horizontal",
            width="content",
        )
        st.markdown(post["content"])

        # 显示附件
        attachments = post.get("attachments", [])
        if attachments:
            st.divider()
            st.caption("📎 附件")
            for att in attachments:
                col_a, col_b, col_c, col_d = st.columns(
                    [0.1, 0.5, 0.2, 0.2], vertical_alignment="center"
                )
                saved_name = att.get("saved_name", "")
                file_bytes = Attachment.get_file(saved_name) if saved_name else b""

                if att.get("type", "").startswith("image/"):
                    col_a.markdown("🖼️")
                elif att.get("type", "").startswith("video/"):
                    col_a.markdown("🎬")
                elif att.get("type", "").startswith("audio/"):
                    col_a.markdown("🎵")
                elif att.get("type", "").startswith("application/"):
                    col_a.markdown("📄")
                else:
                    col_a.markdown("📄")

                col_b.write(f"**{att.get('original_name', '未命名')}**")
                col_c.write(f"{format_size(att.get('size', 0))}")
                with col_d.popover("..."):
                    if file_bytes:
                        if st.toggle("预览"):
                            b64 = base64.b64encode(file_bytes).decode()
                            if att.get("type", "").startswith("image/"):
                                st.image(f"data:{att['type']};base64,{b64}")  # NOQA
                            elif att.get("type", "").startswith("video/"):
                                st.video(f"data:{att['type']};base64,{b64}")  # NOQA
                            elif att.get("type", "").startswith("audio/"):
                                st.audio(f"data:{att['type']};base64,{b64}")  # NOQA

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
        new_comment = col_3.text_input(
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

        if post.get("comments"):
            st.divider()
            comments = post["comments"]
            st.caption(f"💬 评论 ({len(comments)})")
            for comment in comments:
                cfg = user.get_config(comment["userid"])
                if cfg:
                    col_a, col_b = st.columns([0.08, 0.92])
                    col_a.image(cfg["avatar"], width=40)
                    col_b.markdown(f"**{cfg['username']}**")
                    col_b.caption(comment.get("created_at", ""))
                    col_b.markdown(comment["content"])
else:
    st.text("我构建的简易论坛程序....")

    if user.check_by_state():
        with st.container(border=True):
            st.page_link(
                "publish.py",
                label="发布文章",
            )
            st.page_link(
                "user_config.py",
                label="用户配置",
            )
    else:
        st.warning("请登录以解锁更多功能！")
    st.text_input(
        " ", placeholder="搜索", label_visibility="collapsed", icon=":material/search:"
    )
    selected_tag = st.pills("标签", tags)

    # 显示文章列表
    for _post in reversed(Post.get_all()):
        if selected_tag:
            if selected_tag not in _post["tags"]:
                continue

        with st.container(border=True):
            col_1, col_2 = st.columns([0.1, 0.9])
            col_1.image(user.get_config(_post["authorid"])["avatar"], width=55)
            col_2.subheader(f"{_post['title']}")
            if _post["tags"]:
                for tag in _post["tags"]:
                    st.badge(tag)
            userconfig = user.get_config(_post["authorid"])
            if userconfig:
                st.table(
                    {
                        ":material/person: 作者名称": userconfig["username"],  # NOQA
                        ":material/person: 作者ID": userconfig["userid"],
                        ":material/access_time: 发布时间": _post["created_at"],
                        ":material/info: 文章ID": _post["id"],
                    },
                    border="horizontal",
                    width="content",
                )
            st.text(_post["content"][0:40] + "...")
            if st.button("查看详细内容", key=_post["id"]):
                params["post_id"] = str(_post["id"])
                st.rerun()

            attachments = _post.get("attachments", [])
            if attachments:
                st.divider()
                for att in attachments:
                    col_a, col_b, col_c = st.columns([0.1, 0.6, 0.3])
                    saved_name = att.get("saved_name", "")
                    file_bytes = Attachment.get_file(saved_name) if saved_name else b""

                    if att.get("type", "").startswith("image/"):
                        b64 = base64.b64encode(file_bytes).decode()
                        col_a.image(
                            f"data:{att['type']};base64,{b64}",
                            width=60,  # NOQA
                        )  # NOQA
                    elif att.get("type", "").startswith("video/"):
                        col_a.markdown("🎬")
                    elif att.get("type", "").startswith("audio/"):
                        col_a.markdown("🎵")
                    elif att.get("type", "").startswith("application/"):
                        col_a.markdown("📄")
                    else:
                        col_a.markdown("📄")

                    col_b.write(f"**{att.get('original_name', '未命名')}**")
                    col_c.write(f"{format_size(att.get('size', 0))}")
