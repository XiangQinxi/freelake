import os

import pandas as pd
import streamlit as st
from streamlit_file_browser import st_file_browser

from api import (
    Attachment,
    Post,
    User,
    delete_comment_by_id,
    delete_orphaned_attachments,
    delete_orphaned_comments,
    execute_sql,
    format_size,
    get_all_attachments,
    get_attachment_stats,
    get_comment_summary,
    get_orphaned_attachments,
    get_orphaned_comments,
    search_comments,
    sha256,
)

st.page_link("home.py", label="返回主页")

st.subheader("管理员页面")

# region 统计概览
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
# endregion

# region 用户管理
with st.expander("用户管理", expanded=True):
    user = User()

    st.metric("用户总数", user.count(), border=True)
    df = pd.DataFrame(user.get_all()).astype(str)
    st.dataframe(df, use_container_width=True)

    with st.container(border=True):
        col_a, col_b = st.columns(2, vertical_alignment="bottom")
        search_uid = col_a.text_input("搜索用户 ID", placeholder="输入 userid 查询")
        if search_uid and col_b.button("查询"):
            cfg = user.get_config(search_uid)
            if cfg:
                st.json(cfg)
            else:
                st.warning(f"用户 {search_uid} 不存在")

    with st.container(border=True):
        col_a, col_b, col_c = st.columns(3)
        target_uid = col_a.text_input("目标用户 ID", key="role_userid")
        new_role = col_b.selectbox("新角色", ["user", "admin"], key="new_role")
        secret_key = col_c.text_input("密钥", type="password", key="role_secret")
        if st.button("修改角色", key="modify_role_btn"):
            if not target_uid:
                st.warning("请输入目标用户 ID")
            elif not secret_key:
                st.warning("请输入密钥")
            else:
                try:
                    if user.modify_role(target_uid, secret_key, new_role):
                        st.success(f"已修改 {target_uid} 的角色为 {new_role}")
                        st.rerun()
                    else:
                        st.error("修改失败：密钥错误、用户不存在或登录状态已过期")
                except Exception as e:
                    st.error(f"修改失败：{e}")

    with st.container(border=True):
        col_a, col_b = st.columns(2, vertical_alignment="bottom")
        del_uid = col_a.text_input("输入用户 ID 以删除", key="del_userid")
        if col_b.button("删除用户", type="primary", key="del_user_btn"):
            if del_uid:
                state = st.session_state
                state["del_user_confirm"] = del_uid
            else:
                st.warning("请输入用户 ID")
        confirm_uid = st.session_state.get("del_user_confirm")
        if confirm_uid:
            st.warning(f"确定要删除用户 **{confirm_uid}** 吗？此操作不可撤销！")
            col_x, col_y = st.columns(2, vertical_alignment="bottom")
            if col_x.button("确认删除", type="primary", key="confirm_del_user"):
                try:
                    if user.delete_user(confirm_uid):
                        st.success(f"已删除用户 {confirm_uid}")
                        del st.session_state["del_user_confirm"]
                        st.rerun()
                    else:
                        st.error(f"用户 {confirm_uid} 不存在")
                        del st.session_state["del_user_confirm"]
                except Exception as e:
                    st.error(f"删除用户失败：{e}")
                    del st.session_state["del_user_confirm"]
            if col_y.button("取消", key="cancel_del_user"):
                del st.session_state["del_user_confirm"]
                st.rerun()
# endregion

# region 文章管理
with st.expander("文章管理", expanded=True):
    post = Post()

    st.metric("文章总数", post.count(), border=True)
    df = pd.DataFrame(post.get_all()).astype(str)
    st.dataframe(df, use_container_width=True)

    with st.container(border=True):
        col_a, col_b = st.columns([0.8, 0.2], vertical_alignment="bottom")
        search_pid = col_a.text_input("按文章 ID 搜索", placeholder="输入 post_id")
        if col_b.button("搜索", key="search_post_btn"):
            if search_pid:
                try:
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
                        st.warning(f"文章 #{search_pid} 不存在")
                except ValueError:
                    st.error("文章 ID 必须是数字")
                except Exception as e:
                    st.error(f"查询失败：{e}")
            else:
                st.warning("请输入文章 ID")

    with st.container(border=True):
        col_a, col_b = st.columns([0.8, 0.2], vertical_alignment="bottom")
        del_pid = col_a.text_input("输入文章 ID 以删除", key="del_postid")
        if col_b.button("删除文章", type="primary", key="del_post_btn"):
            if del_pid:
                st.session_state["del_post_confirm"] = del_pid
            else:
                st.warning("请输入文章 ID")
        confirm_pid = st.session_state.get("del_post_confirm")
        if confirm_pid:
            st.warning(f"确定要删除文章 **#{confirm_pid}** 吗？此操作不可撤销！")
            col_x, col_y = st.columns(2)
            if col_x.button("确认删除", type="primary", key="confirm_del_post"):
                try:
                    if post.delete(int(confirm_pid)):
                        st.success(f"已删除文章 #{confirm_pid}")
                        del st.session_state["del_post_confirm"]
                        st.rerun()
                    else:
                        st.error(f"文章 #{confirm_pid} 不存在")
                        del st.session_state["del_post_confirm"]
                except Exception as e:
                    st.error(f"删除失败：{e}")
                    del st.session_state["del_post_confirm"]
            if col_y.button("取消", key="cancel_del_post"):
                del st.session_state["del_post_confirm"]
                st.rerun()

    with st.container(border=True):
        st.caption("评论")
        st.info("查看下方独立的「评论管理」面板")
# endregion

# region 评论管理
with st.expander("评论管理", expanded=True):
    st.metric("评论总数", len(get_comment_summary()), border=True)

    tab1, tab2, tab3 = st.tabs(["全部评论", "搜索评论", "孤立评论清理"])

    with tab1:
        df = pd.DataFrame(get_comment_summary()).astype(str)
        st.dataframe(df, use_container_width=True)

        with st.container(border=True):
            col_a, col_b = st.columns([0.8, 0.2], vertical_alignment="bottom")
            del_cid = col_a.text_input("按 comment_id 删除", placeholder="输入评论 ID")
            if col_b.button("删除评论", type="primary", key="del_comment_by_id"):
                if del_cid:
                    st.session_state["del_comment_confirm_id"] = int(del_cid)
                else:
                    st.warning("请输入评论 ID")
            confirm_cid = st.session_state.get("del_comment_confirm_id")
            if confirm_cid:
                st.warning(f"确定要删除评论 #{confirm_cid} 吗？")
                col_x, col_y = st.columns(2)
                if col_x.button("确认删除", type="primary", key="confirm_del_comment"):
                    try:
                        if delete_comment_by_id(confirm_cid):
                            st.success(f"评论 #{confirm_cid} 已删除")
                            del st.session_state["del_comment_confirm_id"]
                            st.rerun()
                        else:
                            st.error(f"评论 #{confirm_cid} 不存在")
                            del st.session_state["del_comment_confirm_id"]
                    except Exception as e:
                        st.error(f"删除失败：{e}")
                        del st.session_state["del_comment_confirm_id"]
                if col_y.button("取消", key="cancel_del_comment"):
                    del st.session_state["del_comment_confirm_id"]
                    st.rerun()

    with tab2:
        col_a, col_b = st.columns([0.8, 0.2], vertical_alignment="bottom")
        keyword = col_a.text_input(
            "关键词", placeholder="按内容或用户 ID 搜索", key="comment_search_kw"
        )
        if col_b.button("搜索", key="search_comment_btn"):
            if keyword:
                result = search_comments(keyword)
                if result:
                    st.dataframe(
                        pd.DataFrame(result).astype(str), use_container_width=True
                    )
                else:
                    st.info("未找到匹配的评论")
            else:
                st.warning("请输入关键词")

    with tab3:
        orphans = get_orphaned_comments()
        col_a, col_b = st.columns([0.7, 0.3])
        col_a.metric("孤立评论数", len(orphans), border=True)
        if col_b.button("清理孤立评论", type="primary"):
            if orphans:
                deleted = delete_orphaned_comments()
                st.success(f"已清理 {deleted} 条孤立评论")
                st.rerun()
            else:
                st.info("没有需要清理的孤立评论")
        if orphans:
            with st.container(border=True):
                st.caption("孤立评论列表（所属文章已被删除）")
                st.dataframe(
                    pd.DataFrame(orphans).astype(str), use_container_width=True
                )
# endregion

# region 附件管理
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
# endregion

# region 系统工具
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
# endregion

# region 文件管理
with st.expander("文件管理", expanded=True):
    event = st_file_browser(
        os.path.dirname(os.path.abspath(__file__)),
        key="deep",
        show_choose_file=True,
        show_delete_file=True,
        show_download_file=False,
        show_new_folder=True,
        show_upload_file=True,
    )
    st.write(event)
# endregion
