"""
FreeLake 发布文章（pages/publish.py）
=====================================

表单式页面：填写标题、内容、选择标签、上传附件（可选）、设置附件专属
密码（可选）后发布新文章。发布成功后跳回首页。
"""
import streamlit as st

from api import Attachment, Post
from const import tags

st.page_link("pages/home.py", label="返回主页")
st.subheader("发布文章")
with st.form(key="publish"):
    new_title = st.text_input(
        "新文章标题", placeholder="请输入标题", label_visibility="collapsed"
    )
    new_content = st.text_area(
        "新文章内容", placeholder="请输入内容", label_visibility="collapsed"
    )
    selected_tags = st.multiselect(
        "标签", tags, default=[tags[0]], accept_new_options=False
    )
    uploaded_files = st.file_uploader(
        "附件（支持图片、文档等各种文件）",
        accept_multiple_files=True,
    )
    attpassword = st.text_input("附件专属密码", type="password")
    if st.form_submit_button("发布"):
        userid = st.session_state.get("userid")
        if not userid:
            st.error("请先登录！")
        elif not new_title:
            st.error("请输入标题！")
        else:
            if not new_content:
                new_content = "这个作者很懒，什么也没写..."
            _attachments = []
            if uploaded_files:
                for uploaded_file in uploaded_files:
                    try:
                        meta = Attachment.save(uploaded_file)
                        _attachments.append(meta)
                    except Exception as e:
                        st.error(f"附件 “{uploaded_file.name}” 保存失败：{e}")

            Post.publish(
                authorid=userid,
                title=new_title,
                content=new_content if new_content else "",
                attachments=_attachments,
                attpassword=attpassword,
                tags=selected_tags,
            )
            st.toast("发布成功！")
            st.switch_page("pages/home.py")
