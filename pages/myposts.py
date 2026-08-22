"""
FreeLake 我的发布（pages/myposts.py）
====================================

展示当前用户发布的全部文章。登录后可见，未登录访问时提示并引导登录。

说明：本页仅通过 st.page_link 跳转（客户端导航），避免服务端导航
（st.switch_page）导致 Cookie 组件重载后卡在「正在加载 Cookies」。
"""
import streamlit as st

from api import Post, User
from api2 import check_by_state

st.page_link("pages/home.py", label=":material/arrow_back: 返回主页")
st.subheader(":material/assignment: 我的发布")

if not check_by_state():
    st.info("请先登录后再查看自己的文章。", icon=":material/login:")
    st.page_link("pages/login.py", label=":material/login: 去登录")
    st.stop()

userid = st.session_state.get("userid")
posts = Post.get_by_author(userid)

if not posts:
    st.info("你还没有发布过文章，去写第一篇吧。", icon=":material/edit_note:")
    st.page_link("pages/publish.py", label=":material/edit_note: 去发布")
    st.stop()

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
            f":material/visibility: {post.get('views', 0)} 次浏览 · "
            f":material/comment: {len(post.get('comments') or [])} 条评论"
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
