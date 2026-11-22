"""
对话框组件（freelake/web/dialogs.py）
=====================================

删除 / 编辑 / 举报等 st.dialog。页面只负责「什么时候打开」，渲染细节在这里，
避免 home.py 又被撑到九百行。
"""

import streamlit as st

from api import Post, Report

state = st.session_state


# region 对话框
@st.dialog("确认删除")
def confirm_delete_post(post_id):
    st.warning("确定要删除这篇文章吗？此操作不可撤销！")
    if st.button("确认删除", type="primary"):
        try:
            if Post.delete(post_id):
                st.toast("文章删除成功！")
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
        "标签",
        tags,
        default=[t for t in (post["tags"] or []) if t in tags],
        accept_new_options=False,
    )
    if st.button("提交修改"):
        if not new_title:
            st.error("请输入标题！")
        elif not new_content:
            st.error("请输入内容！")
        else:
            if Post.edit(post["id"], new_title, new_content, new_tags):
                st.toast("文章修改成功！")
                st.rerun()
            else:
                st.error("文章修改失败！")


@st.dialog("编辑评论")
def edit_comment_dialog(post_id, comment_index, current_content):
    new_content = st.text_area(
        "评论内容", value=current_content, label_visibility="collapsed"
    )
    if st.button("保存修改", type="primary"):
        if not new_content.strip():
            st.error("评论内容不能为空！")
        else:
            if Post.edit_comment(int(post_id), comment_index, new_content):
                st.toast("评论修改成功！")
                st.rerun()
            else:
                st.error("评论修改失败！")


@st.dialog("举报文章")
def report_post_dialog(post):
    st.caption(f"举报文章：**{post['title']}**")
    reason = st.text_area("举报理由", placeholder="请填写举报原因（必填）", height=120)
    if st.button("提交举报", type="primary"):
        reason = reason.strip()
        if not reason:
            st.error("请填写举报理由！")
        else:
            result = Report.add(
                state["userid"],
                postid=int(post["id"]),
                target_userid=post["authorid"],
                content_preview=(post["title"] or "")[:200],
                reason=reason,
            )
            if result:
                st.toast("举报已提交，管理员会尽快处理，感谢反馈！")
                st.rerun()
            else:
                st.warning("你已举报过该文章，管理员正在处理中")


@st.dialog("举报评论")
def report_comment_dialog(post_id, comment):
    st.caption(f"举报评论：{comment['content'][:60]}")
    reason = st.text_area("举报理由", placeholder="请填写举报原因（必填）", height=120)
    if st.button("提交举报", type="primary"):
        reason = reason.strip()
        if not reason:
            st.error("请填写举报理由！")
        else:
            result = Report.add(
                state["userid"],
                postid=int(post_id),
                commentid=int(comment["id"]),
                target_userid=comment["userid"],
                content_preview=comment["content"][:200],
                reason=reason,
            )
            if result:
                st.toast("举报已提交，管理员会尽快处理，感谢反馈！")
                st.rerun()
            else:
                st.warning("你已举报过该评论，管理员正在处理中")


# endregion


# region 常用方法