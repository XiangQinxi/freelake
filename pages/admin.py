"""
FreeLake 管理员页面（pages/admin.py）
=====================================

管理员专属后台，按区域组织：

- 统计概览：用户 / 文章 / 评论 / 附件数量与大小
- 用户管理：查看、搜索、改角色、删除（带二次确认）
- 文章管理：查看、搜索、删除
- 评论管理：全部评论、搜索评论、孤立评论清理
- 附件管理：附件列表、孤立附件清理、附件目录文件浏览
- 系统工具：原始 SQL 查询（⚠️ 谨慎）、SHA256 工具、数据库大小
- 数据导出：用户 / 文章 / 评论 的 CSV 与 JSON 导出

安全说明：页面展示的用户数据已剔除 password / secret_key 等敏感列；
文件浏览器只指向 attachments 目录而非源码目录。
"""
import os

import altair as alt
import pandas as pd
import streamlit as st
from streamlit_file_browser import st_file_browser

from api import (
    ATTACHMENTS_DIR,
    Post,
    User,
    delete_comment_by_id,
    delete_orphaned_attachments,
    delete_orphaned_comments,
    execute_sql,
    export_comments_csv,
    export_comments_json,
    export_posts_csv,
    export_posts_json,
    export_users_csv,
    export_users_json,
    format_size,
    get_all_attachments,
    get_attachment_stats,
    get_comment_summary,
    get_orphaned_attachments,
    get_orphaned_comments,
    search_comments,
    sha256,
)

st.page_link("pages/home.py", label=":material/arrow_back: 返回主页")

st.subheader(":material/security: 管理后台")
state = st.session_state

# region 统计概览
with st.expander("统计概览", expanded=True):
    user = User()
    post = Post()
    stats = get_attachment_stats()
    all_comments = get_comment_summary()
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("用户总数", user.count())
    col2.metric("文章总数", post.count())
    col3.metric("评论总数", len(all_comments))
    col4.metric("附件总数", stats["count"])
    col5.metric("附件总大小", format_size(stats["total_size"]))
# endregion

# region 数据图表
def _daily_counts(records, date_key="created_at"):
    """按发布时间/记录日期聚合为「连续日期」序列（缺失日期补 0）。

    传入 ``post.get_all()`` / ``get_comment_summary()`` 等含
    ``created_at``（``YYYY-MM-DD HH:MM:SS``）的记录列表。
    """
    dates = [r[date_key][:10] for r in records if r.get(date_key)]  # NOQA
    if not dates:
        return pd.DataFrame(columns=["日期", "数量"])
    series = pd.Series(dates).value_counts().sort_index()
    full_idx = pd.date_range(
        pd.to_datetime(series.index.min()),
        pd.to_datetime(series.index.max()),
        freq="D",
    ).strftime("%Y-%m-%d")
    series = series.reindex(full_idx, fill_value=0)
    return pd.DataFrame({"日期": series.index, "数量": series.values})


with st.expander("数据图表", expanded=True):
    posts_all = Post.get_all()
    users_all = User.get_all()
    comments_all = get_comment_summary()

    c1, c2 = st.columns(2)
    with c1:
        with st.container(border=True):
            st.subheader("文章发布趋势")
            df = _daily_counts(posts_all)
            if df.empty:
                st.info("暂无文章数据", icon=":material/bar_chart:")
            else:
                st.area_chart(df, x="日期", y="数量")
    with c2:
        with st.container(border=True):
            st.subheader("评论发表趋势")
            df = _daily_counts(comments_all)
            if df.empty:
                st.info("暂无评论数据", icon=":material/bar_chart:")
            else:
                st.line_chart(df, x="日期", y="数量")

    c3, c4 = st.columns(2)
    with c3:
        with st.container(border=True):
            st.subheader("用户注册趋势")
            df = _daily_counts(users_all)
            if df.empty:
                st.info("暂无用户数据", icon=":material/bar_chart:")
            else:
                st.area_chart(df, x="日期", y="数量")
    with c4:
        with st.container(border=True):
            st.subheader("文章标签分布")
            flat_tags = [t for p in posts_all for t in (p.get("tags") or [])]
            if not flat_tags:
                st.info("暂无标签数据", icon=":material/bar_chart:")
            else:
                tag_df = pd.Series(flat_tags).value_counts().reset_index()
                tag_df.columns = ["标签", "数量"]
                st.bar_chart(tag_df, x="标签", y="数量")

    c5, c6 = st.columns(2)
    with c5:
        with st.container(border=True):
            st.subheader("文章作者 Top 10")
            authors = [p["authorid"] for p in posts_all]
            if not authors:
                st.info("暂无文章数据", icon=":material/bar_chart:")
            else:
                author_df = pd.Series(authors).value_counts().head(10).reset_index()
                author_df.columns = ["作者", "文章数"]
                st.bar_chart(author_df, x="作者", y="文章数", horizontal=True)
    with c6:
        with st.container(border=True):
            st.subheader("用户角色分布")
            roles = [u["role"] for u in users_all]
            if not roles:
                st.info("暂无用户数据", icon=":material/bar_chart:")
            else:
                role_df = pd.Series(roles).value_counts().reset_index()
                role_df.columns = ["角色", "用户数"]
                role_chart = (
                    alt.Chart(role_df)
                    .mark_arc(innerRadius=45)
                    .encode(theta="用户数:Q", color=alt.Color("角色:N"))
                )
                st.altair_chart(role_chart)
# endregion

# region 用户管理
with st.expander("用户管理", expanded=True):
    user = User()

    st.metric("用户总数", user.count(), border=True)
    df = pd.DataFrame(user.get_all()).astype(str)
    # 不展示密码哈希与密钥等敏感列
    for col in ("password", "secret_key"):
        if col in df.columns:
            df = df.drop(columns=[col])
    st.dataframe(df, width="stretch")

    with st.container(border=True):
        col_a, col_b = st.columns(2, vertical_alignment="bottom")
        search_uid = col_a.text_input(
            "搜索用户", placeholder="输入用户 ID 查询", key="search_uid_input"
        )
        if search_uid and col_b.button("查询", key="search_user_btn"):
            cfg = user.get_config(search_uid)
            if cfg:
                st.json(cfg)
            else:
                st.warning(f"用户 {search_uid} 不存在")

    with st.container(border=True):
        col_a, col_b, col_c = st.columns(3)
        target_uid = col_a.text_input("目标用户 ID", key="role_userid")
        new_role = col_b.selectbox("新角色", ["USER", "ADMIN"], key="new_role")
        if st.button("修改角色", key="modify_role_btn"):
            if not target_uid:
                st.warning("请输入目标用户 ID")
            else:
                try:
                    if user.modify(
                        target_uid,
                        None,
                        role=new_role,
                        admin_secret_key=state["secretkey"],
                    ):
                        st.success(f"已修改 {target_uid} 的角色为 {new_role}")
                        st.rerun()
                    else:
                        st.error("修改失败：密钥错误、用户不存在或登录状态已过期")
                except Exception as e:
                    st.error(f"修改失败：{e}")

    with st.container(border=True):
        col_a, col_b = st.columns(2, vertical_alignment="bottom")
        del_uid = col_a.text_input(
            "输入用户 ID 以删除", key="del_userid", placeholder="输入用户 ID"
        )
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
                    if user.delete(confirm_uid, None, admin_secret_key=state["secretkey"]):
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
    st.dataframe(df, width="stretch")

    with st.container(border=True):
        col_a, col_b = st.columns([0.8, 0.2], vertical_alignment="bottom")
        search_pid = col_a.text_input("按文章 ID 搜索", placeholder="输入文章 ID")
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
        del_pid = col_a.text_input("按文章 ID 删除", key="del_postid")
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
        st.caption("评论管理")
        st.info("点击展开下方的「评论管理」面板", icon=":material/chat:")
# endregion

# region 评论管理
with st.expander("评论管理", expanded=True):
    comment_summary = get_comment_summary()
    st.metric("评论总数", len(comment_summary), border=True)

    tab1, tab2, tab3 = st.tabs(["全部评论", "搜索评论", "孤立评论清理"])

    with tab1:
        df = pd.DataFrame(comment_summary).astype(str)
        st.dataframe(df, width="stretch")

        with st.container(border=True):
            col_a, col_b = st.columns([0.8, 0.2], vertical_alignment="bottom")
            del_cid = col_a.text_input("按评论 ID 删除", placeholder="输入评论 ID")
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
                    st.dataframe(pd.DataFrame(result).astype(str), width="stretch")
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
                st.dataframe(pd.DataFrame(orphans).astype(str), width="stretch")
# endregion

# region 附件管理
with st.expander("附件管理", expanded=True):
    all_attachments = get_all_attachments()
    st.metric("附件总数", len(all_attachments), border=True)
    if all_attachments:
        att_df = pd.DataFrame(all_attachments).astype(str)
        st.dataframe(att_df, width="stretch")

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
                fp = os.path.join(ATTACHMENTS_DIR, f)
                size = os.path.getsize(fp)
                st.text(f"{f} ({format_size(size)})")
# endregion

# region 系统工具
with st.expander("系统工具", expanded=False):
    st.warning("⚠️ 仅支持只读 SQL 查询（SELECT / WITH / EXPLAIN / PRAGMA），写操作已被拦截！")
    col1, col2 = st.columns([0.8, 0.2], vertical_alignment="bottom")
    query = col1.text_input(
        "SQL 查询语句", placeholder="例如：SELECT * FROM _user"
    )
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
        st.caption("数据库备份")
        db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data.db")
        if os.path.exists(db_path):
            with open(db_path, "rb") as f:
                db_bytes = f.read()
            col_a, col_b = st.columns([0.5, 0.5])
            col_a.metric("数据库大小", format_size(len(db_bytes)), border=True)
            col_b.download_button(
                label=":material/save: 下载 data.db 备份",
                data=db_bytes,
                file_name="freelake_backup.db",
                mime="application/octet-stream",
                type="primary",
                width="stretch",
            )
            st.caption(
                "恢复方法：先停止应用，再用备份文件覆盖项目根目录的 data.db "
                "（覆盖前请先备份当前文件）。Streamlit Cloud 上文件系统不持久，请自行保管备份。"
            )
# endregion

# region 数据导出
with st.expander("数据导出", expanded=False):
    tab_users, tab_posts, tab_comments = st.tabs(["用户数据", "文章数据", "评论数据"])

    with tab_users:
        col_a, col_b = st.columns(2)
        col_a.download_button(
            label=":material/download: 导出用户 (CSV)",
            data=export_users_csv(),
            file_name="freelake_users.csv",
            mime="text/csv",
            width="stretch",
        )
        col_b.download_button(
            label=":material/download: 导出用户 (JSON)",
            data=export_users_json(),
            file_name="freelake_users.json",
            mime="application/json",
            width="stretch",
        )

    with tab_posts:
        col_a, col_b = st.columns(2)
        col_a.download_button(
            label=":material/download: 导出文章 (CSV)",
            data=export_posts_csv(),
            file_name="freelake_posts.csv",
            mime="text/csv",
            width="stretch",
        )
        col_b.download_button(
            label=":material/download: 导出文章 (JSON)",
            data=export_posts_json(),
            file_name="freelake_posts.json",
            mime="application/json",
            width="stretch",
        )

    with tab_comments:
        col_a, col_b = st.columns(2)
        col_a.download_button(
            label=":material/download: 导出评论 (CSV)",
            data=export_comments_csv(),
            file_name="freelake_comments.csv",
            mime="text/csv",
            width="stretch",
        )
        col_b.download_button(
            label=":material/download: 导出评论 (JSON)",
            data=export_comments_json(),
            file_name="freelake_comments.json",
            mime="application/json",
            width="stretch",
        )
# endregion

# region 文件管理
with st.expander("文件管理", expanded=True):
    st.caption("浏览附件目录（attachments/），可上传或删除附件文件")
    event = st_file_browser(
        ATTACHMENTS_DIR,
        key="deep",
        show_choose_file=True,
        show_delete_file=True,
        show_download_file=False,
        show_new_folder=True,
        show_upload_file=True,
    )
    st.write(event)
# endregion
