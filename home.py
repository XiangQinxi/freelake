import base64
import time

import streamlit as st

from api import Post, format_size, get_attachment_file, save_attachment
from const import tags


params = st.query_params
post_id = params.get("post_id")
if post_id:
    if st.button("返回主页", type="primary"):
        del st.query_params["post_id"]
        st.rerun()
    post = Post.get(int(post_id))
    if post:
        col_1, col_2 = st.columns([0.1, 0.9])
        col_1.image("default_avatar.jpeg", width=55)
        col_2.subheader(f"{post['title']}")
        st.table(
            {
                ":material/person: 作者": post["author"],
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
                col_a, col_b, col_c, col_d = st.columns([0.1, 0.5, 0.2, 0.2], vertical_alignment="center")
                saved_name = att.get("saved_name", "")
                file_bytes = get_attachment_file(saved_name) if saved_name else b""

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
                            type="primary"
                        )
else:
    st.text("我构建的简易论坛程序....")

    with st.container(border=True):
        st.page_link("publish.py", label="发布文章",)

    st.text_input(
        " ", placeholder="搜索", label_visibility="collapsed", icon=":material/search:"
    )
    selected_tag = st.pills("标签", tags)

    # 显示文章列表
    for _post in reversed(Post.get_all()):
        if selected_tag:
            if selected_tag not in _post["tags"]:
                continue
        if not _post["author"]:
            _post["author"] = "匿名"

        with st.container(border=True):
            col_1, col_2 = st.columns([0.1, 0.9])
            col_1.image("default_avatar.jpeg", width=55)
            col_2.subheader(f"{_post['title']}")
            if _post["tags"]:
                for tag in _post["tags"]:
                    st.badge(tag)
            st.table(
                {
                    ":material/person: 作者": _post["author"],
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
                    file_bytes = get_attachment_file(saved_name) if saved_name else b""

                    if att.get("type", "").startswith("image/"):
                        b64 = base64.b64encode(file_bytes).decode()
                        col_a.image(f"data:{att['type']};base64,{b64}", width=60)  # NOQA
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
