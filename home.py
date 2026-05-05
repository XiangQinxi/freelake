import time

import streamlit as st

from api import User, Post

#print("user:", User.get_all())
#print("post:", Post.get_all())

with st.expander("发布你的动态&文章", expanded=True):
    new_content = st.text_area(
        "新文章内容", placeholder="请输入内容", label_visibility="collapsed"
    )
    attachments = st.file_uploader("附件", accept_multiple_files=True)
    if st.button("发布"):
        _attachments = []
        if attachments:
            for attachment in attachments:
                _attachments.append(
                    {
                        "name": attachment.name,
                        "type": attachment.type,
                        "size": attachment.size,
                        "base64": attachment
                    }
                )

        Post.publish(
            author=st.session_state.get("username"),
            content=new_content if new_content else "",
            attachments=_attachments
        )
        st.toast("发布成功！")
        time.sleep(2)
        st.rerun()

for _post in reversed(Post.get_all()):
    if not _post["author"]:
        _post["author"] = "匿名"
    with st.expander(f"{_post['author']}", expanded=True):
        st.caption(f"发布时间：{_post['created_at']}")
        st.text(_post["content"])
