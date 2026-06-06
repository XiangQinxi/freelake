import time

import streamlit as st

from api import Post, Attachment
from const import tags

st.page_link("home.py", label="返回主页")
st.subheader("发布文章")
with st.form(key="publish"):
    new_title = st.text_input(
        "新文章标题", placeholder="请输入标题", label_visibility="collapsed"
    )
    new_content = st.text_area(
        "新文章内容", placeholder="请输入内容", label_visibility="collapsed"
    )
    tags = st.multiselect("标签", tags, default=[tags[0]], accept_new_options=False)
    uploaded_files = st.file_uploader(
        "附件（支持图片、文档等各种文件）",
        accept_multiple_files=True,
    )
    if st.form_submit_button("发布"):
        userid = st.session_state.get("userid")
        if not userid:
            st.error("请先登录！")
        elif not new_title:
            st.error("请输入标题！")
        elif not new_content:
            st.error("请输入内容！")
        else:
            _attachments = []
            if uploaded_files:
                for uploaded_file in uploaded_files:
                    meta = Attachment.save(uploaded_file)
                    _attachments.append(meta)

            Post.publish(
                authorid=userid,
                title=new_title,
                content=new_content if new_content else "",
                attachments=_attachments,
                tags=tags,
            )
            st.toast("发布成功！")
            new_title = None
            new_content = None
            uploaded_files = None
            time.sleep(2)
            st.switch_page("home.py")
