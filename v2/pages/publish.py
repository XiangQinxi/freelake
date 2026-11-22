"""
FreeLake 发布文章（pages/publish.py）
=====================================

填写标题、内容、选择标签、上传附件（可选）、设置附件专属密码（可选）
后发布新文章，发布成功后跳回首页。

「编辑 / 预览」双 Tab 提供 Markdown 实时预览（本页不使用 st.form，
输入变化即时 rerun，页面本身渲染成本很低）。

草稿：登录用户可将未完成内容保存为草稿，稍后从「草稿箱」继续编辑。
草稿仅保留标题 / 内容 / 标签 / 专属密码，附件需在发布前重新上传。
"""

import streamlit as st

from api import Attachment, Draft, Post
from api2 import check_by_state
from const import tags

st.page_link("pages/home.py", label=":material/arrow_back: 返回主页")
st.subheader(":material/edit_note: 发布文章")
st.caption("分享你的想法——标题与内容为必填，附件与专属密码可选。")

userid = st.session_state.get("userid")

# ---- 草稿箱 ----
if check_by_state():
    drafts = Draft.get_drafts(userid)
    with st.expander(f":material/drafts: 草稿箱 ({len(drafts)})", expanded=False):
        if not drafts:
            st.caption("暂无草稿。点击下方「保存草稿」可暂存当前内容。")
        else:
            for d in drafts:
                c1, c2, c3 = st.columns([0.6, 0.2, 0.2], vertical_alignment="center")
                c1.markdown(f"**{d.get('title') or '（无标题）'}**")
                c1.caption(d.get("updated_at", ""))
                if c2.button("继续编辑", key=f"load_draft_{d['id']}"):
                    # 预填表单（仅保留合法标签），并在下次 rerun 后由同名 key 读取
                    st.session_state["publish_title"] = d.get("title") or ""
                    st.session_state["publish_content"] = d.get("content") or ""
                    st.session_state["publish_tags"] = [
                        t for t in (d.get("tags") or []) if t in tags
                    ]
                    st.session_state["publish_attpassword"] = d.get("attpassword") or ""
                    st.session_state["editing_draft_id"] = d["id"]
                    st.rerun()
                if c3.button("删除", key=f"del_draft_{d['id']}"):
                    Draft.delete(userid, d["id"])
                    st.rerun()

with st.container(border=True):
    tab_edit, tab_preview = st.tabs(
        [":material/edit: 编辑", ":material/visibility: 预览"]
    )

    with tab_edit:
        new_title = st.text_input(
            "新文章标题",
            placeholder="请输入标题",
            label_visibility="collapsed",
            key="publish_title",
        )
        new_content = st.text_area(
            "新文章内容",
            placeholder="请输入内容，支持 Markdown 排版",
            label_visibility="collapsed",
            height=200,
            key="publish_content",
        )
        selected_tags = st.multiselect(
            "选择标签",
            tags,
            default=[tags[0]],
            accept_new_options=False,
            key="publish_tags",
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
    key="publish_attpassword",
)

col_pub, col_draft = st.columns(2)
if col_pub.button(":material/send: 发布文章", type="primary", width="stretch"):
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
        # 发布成功后删除正编辑的草稿
        editing_draft_id = st.session_state.get("editing_draft_id")
        if editing_draft_id:
            Draft.delete(userid, editing_draft_id)
            st.session_state["editing_draft_id"] = None
        st.toast("发布成功！")
        st.switch_page("pages/home.py")

if col_draft.button(":material/save: 保存草稿", width="stretch"):
    if not userid:
        st.error("请先登录！")
    else:
        editing_draft_id = st.session_state.get("editing_draft_id")
        did = Draft.save(
            userid,
            new_title,
            new_content,
            selected_tags,
            attpassword,
            draft_id=editing_draft_id,
        )
        st.session_state["editing_draft_id"] = did
        st.toast("草稿已保存，可在草稿箱继续编辑")
