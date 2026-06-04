import base64
import time

import streamlit as st

from api import User, Post, save_attachment, get_attachment_file


def _format_size(size_bytes: int) -> str:
    """将字节数转换为人类可读的文件大小格式"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    else:
        return f"{size_bytes / 1024 / 1024 / 1024:.1f} GB"


with st.expander("发布你的动态&文章", expanded=True):
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
        else:
            _attachments = []
            if uploaded_files:
                for uploaded_file in uploaded_files:
                    meta = save_attachment(uploaded_file)
                    _attachments.append(meta)

            Post.publish(
                author=username,
                content=new_content if new_content else "",
                attachments=_attachments,
            )
            st.toast("发布成功！")
            time.sleep(2)
            st.rerun()

# 显示文章列表
for _post in reversed(Post.get_all()):
    if not _post["author"]:
        _post["author"] = "匿名"
    with st.expander(f"{_post['author']}", expanded=True):
        st.caption(f"发布时间：{_post['created_at']}")
        st.text(_post["content"])

        # 显示附件
        attachments = _post.get("attachments", [])
        if attachments:
            st.divider()
            st.caption("📎 附件")
            for att in attachments:
                col_a, col_b, col_c = st.columns([0.1, 0.6, 0.3])
                saved_name = att.get("saved_name", "")
                file_bytes = get_attachment_file(saved_name) if saved_name else b""

                # 如果是图片，显示缩略图
                if att.get("type", "").startswith("image/"):
                    b64 = base64.b64encode(file_bytes).decode()
                    col_a.image(f"data:{att['type']};base64,{b64}", width=60)
                else:
                    col_a.markdown("📄")

                col_b.write(f"**{att.get('original_name', '未命名')}**")
                col_c.write(f"{_format_size(att.get('size', 0))}")

                # 提供下载按钮
                if file_bytes:
                    st.download_button(
                        label=f"⬇️ 下载 {att.get('original_name', '文件')}",
                        data=file_bytes,
                        file_name=att.get("original_name", "download"),
                        mime=att.get("type") or "application/octet-stream",
                        key=f"dl_{_post['id']}_{saved_name}",
                    )
