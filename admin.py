import os

import pandas as pd
import streamlit as st
from streamlit_file_browser import st_file_browser

from api import (
    Attachment,
    Post,
    User,
    execute_sql,
    format_size,
    get_all_attachments,
    get_attachment_stats,
    get_comment_summary,
    get_orphaned_attachments,
    delete_orphaned_attachments,
    sha256,
)

st.page_link("home.py", label="返回主页")

st.subheader("管理员页面")

with st.expander("统计概览", expanded=True):
    user = User()
    stats = get_attachment_stats()
    all_comments = get_comment_summary()
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("用户总数", user.count())
    col2.metric("文章总数", Post.count())
    col3.metric("评论总数", len(all_comments))
    col4.metric("附件总数", stats["count"])
    col5.metric("附件总大小", format_size(stats["total_size"]))

with st.expander("用户管理", expanded=True):
    user = User()

    st.metric("用户总数", user.count(), border=True)
    df = pd.DataFrame(user.get_all()).astype(str)
    st.dataframe(df, use_container_width=True)

    with st.container(border=True):
        col_a, col_b = st.columns(2)
        search_uid = col_a.text_input("搜索用户 ID", placeholder="输入 userid 查询")
        if search_uid and col_b.button("查询"):
            cfg = user.get_config(search_uid)
            if cfg:
                st.json(cfg)
            else:
                st.warning("用户不存在")

    with st.container(border=True):
        col_a, col_b, col_c = st.columns(3)
        target_uid = col_a.text_input("目标用户 ID", key="role_userid")
        new_role = col_b.selectbox("新角色", ["user", "admin"], key="new_role")
        secret_key = col_c.text_input("密钥", type="password", key="role_secret")
        if st.button("修改角色", key="modify_role_btn"):
            if user.modify_role(target_uid, secret_key, new_role):
                st.success(f"已修改 {target_uid} 的角色为 {new_role}")
                st.rerun()
            else:
                st.error("修改失败，请检查密钥或用户 ID")

    with st.container(border=True):
        col_a, col_b = st.columns(2)
        del_uid = col_a.text_input("输入用户 ID 以删除", key="del_userid")
        if col_b.button("删除用户", type="primary", key="del_user_btn"):
            if del_uid:
                if user.delete_user(del_uid):
                    st.success(f"已删除用户 {del_uid}")
                    st.rerun()
                else:
                    st.error("用户不存在")
            else:
                st.warning("请输入用户 ID")

with st.expander("文章管理", expanded=True):
    post = Post()

    st.metric("文章总数", post.count(), border=True)
    df = pd.DataFrame(post.get_all()).astype(str)
    st.dataframe(df, use_container_width=True)

    with st.container(border=True):
        col_a, col_b = st.columns([0.8, 0.2])
        search_pid = col_a.text_input("按文章 ID 搜索", placeholder="输入 post_id")
        if col_b.button("搜索", key="search_post_btn"):
            if search_pid:
                p = post.get(int(search_pid))
                if p:
                    st.json(
                        {
                            "id": p["id"],
                            "title": p["title"],
                            "authorid": p["authorid"],
                            "content": p["content"][:200]
                            + ("..." if len(p["content"]) > 200 else ""),
                            "created_at": p["created_at"],
                            "tags": p["tags"],
                            "attachments_count": len(p.get("attachments", [])),
                            "comments_count": len(p.get("comments", [])),
                        }
                    )
                else:
                    st.warning("文章不存在")
            else:
                st.warning("请输入文章 ID")

    with st.container(border=True):
        col_a, col_b = st.columns([0.8, 0.2])
        del_pid = col_a.text_input("输入文章 ID 以删除", key="del_postid")
        if col_b.button("删除文章", type="primary", key="del_post_btn"):
            if del_pid:
                if post.delete(int(del_pid)):
                    st.success(f"已删除文章 #{del_pid}")
                    st.rerun()
                else:
                    st.error("文章不存在")
            else:
                st.warning("请输入文章 ID")

    with st.container(border=True):
        st.caption("评论管理")
        comments_df = pd.DataFrame(get_comment_summary()).astype(str)
        st.dataframe(comments_df, use_container_width=True)
        col_a, col_b, col_c = st.columns(3)
        c_post_id = col_a.text_input("文章 ID", key="comment_postid")
        c_index = col_b.text_input("评论索引", key="comment_index")
        if col_c.button("删除评论", type="primary", key="del_comment_btn"):
            if c_post_id and c_index:
                if post.delete_comment(int(c_post_id), int(c_index)):
                    st.success("评论已删除")
                    st.rerun()
                else:
                    st.error("删除失败，请检查文章 ID 和评论索引")
            else:
                st.warning("请填写文章 ID 和评论索引")

with st.expander("附件管理", expanded=True):
    all_attachments = get_all_attachments()
    st.metric("附件总数", len(all_attachments), border=True)
    if all_attachments:
        att_df = pd.DataFrame(all_attachments).astype(str)
        st.dataframe(att_df, use_container_width=True)

    orphans = get_orphaned_attachments()
    col_a, col_b = st.columns([0.7, 0.3])
    col_a.metric("孤立附件数", len(orphans), border=True)
    if col_b.button("清理孤立附件", type="primary"):
        if orphans:
            deleted = delete_orphaned_attachments()
            st.success(f"已清理 {deleted} 个孤立附件")
            st.rerun()
        else:
            st.info("没有需要清理的孤立附件")

    if orphans:
        with st.container(border=True):
            st.caption("孤立文件列表")
            for f in orphans:
                fp = os.path.join("attachments", f)
                size = os.path.getsize(os.path.join(os.path.dirname(__file__), fp))
                st.text(f"{f} ({format_size(size)})")

with st.expander("系统工具", expanded=False):
    col1, col2 = st.columns([0.8, 0.2], vertical_alignment="bottom")
    query = col1.text_input("请输入SQL查询语句", placeholder="例如：SELECT * FROM user")
    if col2.button("执行查询"):
        if query:
            try:
                result = execute_sql(query)
                st.write(result)
            except Exception as e:
                st.error(f"查询执行失败：{e}")
        else:
            st.warning("请输入SQL查询语句")

    with st.container(border=True):
        st.caption("SHA256 加密工具")
        original_text = st.text_input("加密文本", key="sha_input")
        if original_text:
            st.code(sha256(original_text))

    with st.container(border=True):
        db_path = os.path.join(os.path.dirname(__file__), "data.db")
        if os.path.exists(db_path):
            st.metric("数据库大小", format_size(os.path.getsize(db_path)), border=True)

with st.expander("文件管理", expanded=True):
    event = st_file_browser(
        os.path.dirname(os.path.abspath(__file__)),
        key="deep",
        show_choose_file=True,
        show_delete_file=True,
        show_download_file=False,
        show_new_folder=True,
        show_upload_file=False,
    )
    st.write(event)
