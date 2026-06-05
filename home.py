import base64
import time

import streamlit as st

from api import Post, format_size, get_attachment_file, save_attachment


params = st.query_params
post_id = params.get("post_id")
if post_id:
    if st.button("返回主页", type="primary"):
        del st.query_params["post_id"]
        st.rerun()
    post = Post.get(int(post_id))
    if post:
        print(post)
        st.subheader(f"{post['title']}")
        st.table(
            {
                ":material/person: 作者": post["author"],
                ":material/access_time: 发布时间": post["created_at"],
                ":material/info: 文章ID": post["id"],
            },
            border="horizontal",
            width="content",
        )
        st.text(post["content"])

        # 显示附件
        attachments = post.get("attachments", [])
        if attachments:
            st.divider()
            st.caption("📎 附件")
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

                # 提供下载按钮
                if file_bytes:
                    st.download_button(
                        label=f"⬇️ 下载 {att.get('original_name', '文件')}",
                        data=file_bytes,
                        file_name=att.get("original_name", "download"),
                        mime=att.get("type") or "application/octet-stream",
                        key=f"dl_{post['id']}_{saved_name}",
                        type="primary"
                    )
else:
    st.text("我构建的简易论坛程序....")

    with st.expander("发布你的动态&文章", expanded=True):
        new_title = st.text_input(
            "新文章标题", placeholder="请输入标题", label_visibility="collapsed"
        )
        new_content = st.text_area(
            "新文章内容", placeholder="请输入内容", label_visibility="collapsed"
        )
        uploaded_files = st.file_uploader(
            "附件（支持图片、文档等各种文件）",
            accept_multiple_files=True,
        )
        if st.button("发布"):
            username = st.session_state.get("username")
            if not username:
                st.error("请先登录！")
            elif not new_title:
                st.error("请输入标题！")
            elif not new_content:
                st.error("请输入内容！")
            else:
                _attachments = []
                if uploaded_files:
                    for uploaded_file in uploaded_files:
                        meta = save_attachment(uploaded_file)
                        _attachments.append(meta)

                Post.publish(
                    author=username,
                    title=new_title,
                    content=new_content if new_content else "",
                    attachments=_attachments,
                )
                st.toast("发布成功！")
                new_title = None
                new_content = None
                uploaded_files = None
                time.sleep(2)
                st.rerun()


    st.text_input(
        " ", placeholder="搜索", label_visibility="collapsed", icon=":material/search:"
    )

    # 显示文章列表
    for _post in reversed(Post.get_all()):
        if not _post["author"]:
            _post["author"] = "匿名"
        with st.container(border=True):
            st.subheader(f"{_post['title']}")
            st.table(
                {
                    ":material/person: 作者": _post["author"],
                    ":material/access_time: 发布时间": _post["created_at"],
                    ":material/info: 文章ID": _post["id"],
                },
                border="horizontal",
                width="content",
            )
            if st.button(_post["content"][0:40] + "......", key=_post["id"]):
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
                        b64 = base64.b64encode(file_bytes).decode()
                        col_a.video(f"data:{att['type']};base64,{b64}", width=60)  # NOQA
                    elif att.get("type", "").startswith("audio/"):
                        col_a.markdown("🎵")
                        b64 = base64.b64encode(file_bytes).decode()
                        col_a.audio(f"data:{att['type']};base64,{b64}", width=60)  # NOQA
                    elif att.get("type", "").startswith("application/"):
                        col_a.markdown("📄")
                    else:
                        col_a.markdown("📄")

                    col_b.write(f"**{att.get('original_name', '未命名')}**")
                    col_c.write(f"{format_size(att.get('size', 0))}")
