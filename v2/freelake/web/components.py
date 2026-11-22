"""
通用渲染组件（freelake/web/components.py）
=========================================

列表卡片、附件预览、点赞/收藏按钮条：首页与我的收藏页共用同一套实现，
改样式只改这里（以前两处各写一份，容易走样）。
"""

import base64

import streamlit as st

from api import (
    Attachment,
    Bookmark,
    Like,
    Post,
    User,
    format_size,
    get_avatar_bytes,
)
from api2 import check_by_state
from const import admin

from freelake.web.dialogs import (
    confirm_delete_post,
    edit_post,
    report_post_dialog,
)
from freelake.web.text import highlight_keyword, highlight_mentions

state = st.session_state
user = User()


def _current_userconfig() -> dict:
    """当前登录用户的配置（未登录时返回空字典）。"""
    userid = state.get("userid")
    return (user.get_config(userid) or {}) if userid else {}


def avatar_link(container, avatar_name, userid, width):
    """把头像渲染成可点击图，点击后在同一标签页跳转到用户主页（?user_id=）。

    ``st.image(link=...)`` 在不同环境下可能新开标签页；这里改用不带 ``target``
    的 ``<a>`` 包裹 ``img``，确保同标签跳转。
    """
    b64 = base64.b64encode(
        get_avatar_bytes(avatar_name or "default_avatar.jpeg")
    ).decode()
    container.html(
        f'<a href="?user_id={userid}" title="查看个人主页" '
        f'style="display:inline-block;line-height:0;">'
        f'<img src="data:image/jpeg;base64,{b64}" '
        f'style="width:{width}px;height:{width}px;border-radius:50%;'
        f'object-fit:cover;display:inline-block;"/>'
        f"</a>"
    )


state = st.session_state



def basic_information(post, _config=None, compact=False):
    """展示文章的基础信息卡片：作者头像、标题、操作菜单、标签与元信息。

    _config: 作者的用户配置字典；传入时跳过重复查询（列表页批量传入）。
    compact: 列表页紧凑模式——以一行说明文字替代元信息表格。
    """
    col_1, col_2, col_3 = st.columns([0.1, 0.8, 0.1])

    if _config is None:
        _config = user.get_config(post["authorid"])
    if _config:
        avatar = _config["avatar"]
        username = _config["username"]
    else:
        avatar = "default_avatar.jpeg"
        username = "用户已注销"

    avatar_link(col_1, avatar, post["authorid"], 55)
    col_2.subheader(post["title"])
    if check_by_state():
        # 更多菜单：作者/管理员可编辑、删除；其他登录用户可举报；所有登录用户可点赞、收藏
        menu_options = []
        _me = _current_userconfig()
        if post["authorid"] == state["userid"] or _me.get("role") == admin:
            menu_options.append(":material/edit: 编辑")
            menu_options.append(":material/delete: 删除")
        elif _me.get("userid") != post["authorid"]:
            menu_options.append(":material/report: 举报")
        liked = Like.is_liked(post["id"], state["userid"])
        bookmarked = Bookmark.is_bookmarked(post["id"], state["userid"])
        menu_options.append(
            ":material/favorite: 取消点赞"
            if liked
            else ":material/favorite_border: 点赞"
        )
        menu_options.append(
            ":material/bookmark: 取消收藏"
            if bookmarked
            else ":material/bookmark_border: 收藏"
        )
        action = col_3.menu_button(
            "",
            options=menu_options,
            icon=":material/more_vert:",
            key=f"{post['id']}.menu",
            type="tertiary",
        )
        match action:
            case ":material/edit: 编辑":
                edit_post(post)
            case ":material/delete: 删除":
                confirm_delete_post(post["id"])
            case ":material/report: 举报":
                report_post_dialog(post)
            case ":material/favorite: 取消点赞" | ":material/favorite_border: 点赞":
                Like.toggle(post["id"], state["userid"])
                st.rerun()
            case ":material/bookmark: 取消收藏" | ":material/bookmark_border: 收藏":
                Bookmark.toggle(post["id"], state["userid"])
                st.rerun()
    if post["tags"]:
        if compact:
            st.markdown(" ".join(f":blue-badge[{tag}]" for tag in post["tags"]))
        else:
            for tag in post["tags"]:
                st.badge(tag, color="primary")
    if compact:
        st.caption(
            f":material/person: {username} · "
            f":material/access_time: {post['created_at']} · "
            f":material/visibility: {post.get('views', 0)} 次浏览"
        )
    else:
        st.table(
            {
                ":material/person: 作者名称": username,  # NOQA
                ":material/access_time: 发布时间": post["created_at"],
                ":material/visibility: 浏览量": post.get("views", 0),
                ":material/info: 文章ID": post["id"],
            },
            border="horizontal",
            width="content",
        )



def preview(att, container, saved_name, attpassword="", auto_preview=True):
    """在指定容器中渲染附件的缩略图/类型图标。

    - 图片：可访问时生成缩略图，否则显示图片占位图标
    - 视频/音频/其他：显示对应的 Material 图标
    attpassword: 附件专属密码（哈希），为空表示无需密码。
    """
    if att.get("type", "").startswith("image/"):
        if (
            not attpassword
            or not auto_preview
            or state["attpassword"] == attpassword
            or state["userid"] == post.get("authorid")
            or _current_userconfig().get("role") == admin
        ):
            b64 = Attachment.get_thumbnail_base64(saved_name, max_width=200)  # NOQA
            container.image(
                f"data:image/jpeg;base64,{b64}",  # NOQA
                width=60,  # NOQA
            )  # NOQA
        else:
            container.markdown(":material/image:")
    elif att.get("type", "").startswith("video/"):
        container.markdown(":material/smart_display:")
    elif att.get("type", "").startswith("audio/"):
        container.markdown(":material/headphones:")
    elif att.get("type", "").startswith("application/"):
        container.markdown(":material/insert_drive_file:")
    else:
        container.markdown(":material/insert_drive_file:")



def like_and_bookmarked(post):
    """详情页点赞与收藏按钮组（未激活 secondary，已激活 primary 更醒目）。"""
    with st.container(horizontal=True):
        liked = (
            Like.is_liked(post["id"], state["userid"]) if state.get("userid") else False
        )
        if st.button(
            f"{':material/favorite:' if liked else ':material/favorite_border:'} {Like.count(post['id'])}",
            key=f"detail_like_{post['id']}",
            type="primary" if liked else "secondary",
        ):
            if state.get("userid"):
                Like.toggle(post["id"], state["userid"])
                st.rerun()
            else:
                st.warning("请先登录")
        bookmarked = (
            Bookmark.is_bookmarked(post["id"], state["userid"])
            if state.get("userid")
            else False
        )
        if st.button(
            f"{':material/bookmark:' if bookmarked else ':material/bookmark_border:'} {Bookmark.count(post['id'])}",
            key=f"detail_bm_{post['id']}",
            type="primary" if bookmarked else "secondary",
        ):
            if state.get("userid"):
                Bookmark.toggle(post["id"], state["userid"])
                st.rerun()
            else:
                st.warning("请先登录")


# endregion

