"""
FreeLake 发布文章（pages/publish.py）
=====================================

填写标题、内容、选择标签、上传附件（可选）、设置附件专属密码（可选）
后发布新文章，发布成功后跳回首页。

「编辑 / 预览」双 Tab 提供 Markdown 实时预览（本页不使用 st.form，
输入变化即时 rerun，页面本身渲染成本很低）。
"""
import streamlit as st

from api import Attachment, Post
from const import tags

st.page_link("pages/home.py", label=":material/arrow_back: 返回主页")
st.subheader(":material/edit_note: 发布文章")
st.caption("分享你的想法——标题与内容为必填，附件与专属密码可选。")

with st.container(border=True):
    tab_edit, tab_preview = st.tabs(
        [":material/edit: 编辑", ":material/visibility: 预览"]
    )

    with tab_edit:
        new_title = st.text_input(
            "新文章标题", placeholder="请输入标题", label_visibility="collapsed"
        )
        new_content = st.text_area(
            "新文章内容",
            placeholder="请输入内容，支持 Markdown 排版",
            label_visibility="collapsed",
            height=200,
        )
        selected_tags = st.multiselect(
            "选择标签", tags, default=[tags[0]], accept_new_options=False
        )

    with tab_preview:
        if new_content:
            st.markdown(new_content)
        else:
            st.caption("（预览区）输入内容后实时渲染 Markdown 效果")
        if selected_tags:
            st.divider()
            st.markdown(" ".join(f":blue-badge[{tag}]" for tag in selected_tags))

uploaded_files = st.file_uploader(
    "上传附件（可选）",
    help="支持图片、文档等任意文件，可一次选择多个",
    accept_multiple_files=True,
)
attpassword = st.text_input(
    "附件专属密码（可选）",
    help="设置后，访问者需输入密码才能查看附件；留空表示公开",
    type="password",
)

if st.button(":material/send: 发布文章", type="primary"):
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
