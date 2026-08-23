"""
FreeLake 我的收藏（pages/bookmarks.py）
======================================

展示当前用户收藏的全部文章（按收藏顺序倒序为发布倒序）。登录后可见，
未登录访问时提示并引导登录。

说明：本页仅通过 st.page_link 跳转（客户端导航），避免服务端导航
（st.switch_page）导致 Cookie 组件重载后卡在「正在加载 Cookies」。
"""

import streamlit as st

from api import Bookmark, Post, User
from api2 import check_by_state

st.page_link("pages/home.py", label=":material/arrow_back: 返回主页")
st.subheader(":material/bookmark: 我的收藏")

if not check_by_state():
    st.info("请先登录后再查看收藏。", icon=":material/login:")
    st.page_link("pages/login.py", label=":material/login: 去登录")
    st.stop()

userid = st.session_state.get("userid")
bookmarked_ids = Bookmark.get_bookmarked_post_ids(userid)
if not bookmarked_ids:
    st.info("还没有收藏任何文章，去首页逛逛吧。", icon=":material/bookmark_border:")
    st.stop()

posts = Post.get_filtered_paginate(post_ids=bookmarked_ids, page=1, page_size=100)
author_configs = User.get_configs(p["authorid"] for p in posts)

for post in posts:
    with st.container(border=True):
        cfg = author_configs.get(post["authorid"])
        st.markdown(f"### {post['title']}")
        if post.get("tags"):
            st.markdown(" ".join(f":blue-badge[{tag}]" for tag in post["tags"]))
        st.caption(
            f":material/person: {cfg['username'] if cfg else '用户已注销'} · "
            f":material/access_time: {post['created_at']} · "
            f":material/visibility: {post.get('views', 0)} 次浏览"
        )
        snippet = (post["content"] or "").strip()
        if len(snippet) > 60:
            snippet = snippet[:60] + "…"
        if snippet:
            st.markdown(snippet)
        st.page_link(
            "pages/home.py",
            label=":material/arrow_forward: 阅读全文",
            query_params={"post_id": str(post["id"])},
        )
