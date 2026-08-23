"""
FreeLake 首页（pages/home.py）
==============================

核心页面，根据 URL 查询参数承担三种视图：

- ``?user_id=xxx``        —— 查看某用户的个人资料
- ``?post_id=xxx``        —— 查看文章详情（内容、附件、评论区）
- 无参数                  —— 主页：搜索 / 标签筛选 / 收藏筛选 + 文章列表分页

性能约定：列表页对作者配置与「最新一条评论」使用批量查询
（``User.get_configs`` / ``Post.get_last_comments``），附件文件只读取一次，
避免 N+1 查询与重复读盘。
"""

import base64
import datetime
import io
import json
import re
from zipfile import ZipFile

import streamlit as st
from streamlit_video_background import render_video_background

from api import (Attachment, Bookmark, Like, Post, Report, User, format_size,
                 get_avatar_bytes, save_avatar, sha256)
from api2 import check_by_state
from const import admin, tags

# 主页排序选项（menu_button 展开项）与对应的查询排序键
SORT_OPTIONS = ("按日期排序", "按点赞量排序", "按浏览量排序", "按收藏量排序")
SORT_KEYS = {
    "按日期排序": "date",
    "按点赞量排序": "likes",
    "按浏览量排序": "views",
    "按收藏量排序": "bookmarks",
}
DEFAULT_SORT = "按日期排序"
DEFAULT_DATE_START = datetime.date(2026, 8, 1)  # 发布日期起点

# 主页动态视频背景由 `streamlit_video_background` 扩展包提供（在下方主页视图处调用）。
# 通过 `pip install -e streamlit-video-background` 已安装，可独立分发。


def highlight_keyword(text: str, keyword: str) -> str:
    """将关键词匹配片段用 Streamlit 彩色文本（:red[...]）高亮；无关键词或空文本则原样返回。"""
    if not keyword or not text:
        return text
    pattern = re.compile(re.escape(keyword), re.IGNORECASE)
    return pattern.sub(lambda m: f":red[{m.group(0)}]", text)


def highlight_mentions(text: str) -> str:
    """把评论/内容中的 @用户名 渲染为醒目徽章（不做真实用户解析，仅视觉高亮）。"""
    if not text:
        return text
    # 匹配中英文、数字、下划线、连字符组成的 @提及
    return re.sub(
        r"@([A-Za-z0-9_\-\u4e00-\u9fa5]+)",
        r":blue-badge[@\1]",
        text,
    )


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


user = User()
params = st.query_params
state = st.session_state
post_id = params.get("post_id")
user_id = params.get("user_id")  # 用于查看某人的简介
search_keyword = params.get("search_keyword", None)
userconfig = user.get_config(state["userid"])
if userconfig is None:
    userconfig = {
        "userid": None,
        "username": None,
        "created_at": None,
        "description": None,
        "role": None,
        "avatar": None,
    }

if "attpassword" not in state:
    state["attpassword"] = sha256("")


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
        if post["authorid"] == state["userid"] or userconfig["role"] == admin:
            menu_options.append(":material/edit: 编辑")
            menu_options.append(":material/delete: 删除")
        elif userconfig.get("userid") != post["authorid"]:
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
            or userconfig.get("role") == admin
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


# region 用户信息查询
if user_id:
    if st.button(":material/arrow_back: 返回主页", type="primary"):
        del st.query_params["user_id"]
        st.rerun()
    userconfig = user.get_config(user_id)
    if userconfig:
        # 头像 + 名字横幅
        col_h, col_i = st.columns([0.24, 0.76], vertical_alignment="center")
        col_h.image(get_avatar_bytes(userconfig["avatar"]), width=150)
        with col_i:
            st.markdown(f"### {userconfig.get('username')}")
            role_name = "管理员" if userconfig.get("role") == admin else "普通用户"
            st.caption(
                f":material/badge: {role_name} · 注册于 {userconfig.get('created_at')}"
            )
            st.markdown(userconfig.get("description") or "这个用户很懒，什么也没留下~")
        st.divider()

        # 数据统计
        stats = Post.get_author_stats(user_id)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("发布文章", stats["post_count"], border=True)
        c2.metric("总浏览", stats["views"], border=True)
        c3.metric("总获赞", stats["likes"], border=True)
        c4.metric("总获藏", stats["bookmarks"], border=True)
        st.divider()

        # 账号设置（仅本人可见；点击头像或个人设置按钮到达本页）
        is_self = bool(state.get("userid") and state.get("userid") == user_id)
        if is_self:
            with st.container(border=True):
                st.caption(":material/manage_accounts: 账号设置")
                # —— 内联编辑资料（直接在页面展示，不弹新窗口）——
                st.markdown("#### :material/account_circle: 编辑资料")
                new_avatar = st.file_uploader(
                    "上传新头像", type="image/*", label_visibility="collapsed"
                )
                if new_avatar:
                    st.image(new_avatar, width=150)
                new_username = st.text_input(
                    "用户名",
                    placeholder="请输入用户名",
                    value=userconfig.get("username"),  # NOQA
                )
                new_description = st.text_area(
                    "自我介绍",
                    placeholder="请输入自我介绍",
                    value=userconfig.get("description"),  # NOQA
                )
                if st.button(":material/save: 保存修改", type="primary"):
                    if not new_username or not new_description:
                        st.error("请输入用户名和自我介绍！")
                    else:
                        meta = save_avatar(new_avatar) if new_avatar else {}
                        ok = user.modify(
                            state.get("userid"),
                            state.get("password"),
                            username=new_username,
                            description=new_description,
                            avatar=meta.get("path") if new_avatar else None,
                        )
                        if ok:
                            st.toast("用户信息修改成功！")
                            st.rerun()
                        else:
                            st.error("修改失败：请检查密码或登录状态已过期")
                # —— 修改密码 / 退出登录 ——
                col_pass, col_log = st.columns(2)
                with col_pass.popover(
                    ":material/password: 修改密码", type="secondary"
                ):
                    original_password = st.text_input(
                        "原密码", placeholder="请输入原密码", type="password"
                    )
                    new_password = st.text_input(
                        "新密码", placeholder="请输入新密码", type="password"
                    )
                    if st.button("提交"):
                        if not original_password or not new_password:
                            st.error("请输入原密码和新密码！")
                        else:
                            ok = user.modify(
                                state.get("userid"),
                                password=original_password,
                                new_password=new_password,
                            )
                            if ok:
                                state["password"] = new_password
                                state["cookies"]["password"] = new_password
                                st.toast("密码修改成功！")
                                st.rerun()
                            else:
                                st.error("原密码错误，修改失败！")
                if col_log.button(
                    ":material/logout: 退出登录", type="primary", width="stretch"
                ):
                    st.switch_page("pages/logout.py")
            st.divider()

        # 该用户发布的文章列表
        st.markdown("### :material/article: 发布的文章")
        author_posts = Post.get_by_author(user_id)
        if not author_posts:
            st.caption("还没有发布过文章。")
        else:
            for p in author_posts[:50]:
                with st.container(border=True):
                    basic_information(p, userconfig, compact=True)
                    snippet = (p["content"] or "").strip()
                    if len(snippet) > 40:
                        snippet = snippet[:40] + "…"
                    if snippet:
                        st.markdown(snippet)
                    if st.button(
                        "阅读全文",
                        icon=":material/arrow_forward:",
                        key=f"author_view_{p['id']}",
                    ):
                        params["post_id"] = str(p["id"])
                        del params["user_id"]  # 从个人主页进入详情
                        st.rerun()
    else:
        st.error("该用户不存在！")
# endregion
else:
    # region 文章信息查询
    if post_id:
        if st.button(":material/arrow_back: 返回主页", type="primary"):
            del st.query_params["post_id"]
            st.rerun()
        try:
            # 浏览量 +1（每个会话只计一次，避免页面交互 rerun 重复计数）
            if not state.get(f"viewed_{post_id}"):
                Post.add_view(int(post_id))
                state[f"viewed_{post_id}"] = True
            post = Post.get(int(post_id))
        except (TypeError, ValueError):
            post = None
        if post:
            basic_information(post)

            st.markdown(highlight_keyword(post["content"], search_keyword or ""))

            like_and_bookmarked(post)

            # 显示附件
            attachments = post.get("attachments", [])
            if attachments:
                st.divider()

                attpassword = post.get("attpassword")

                if (
                    (attpassword and state["attpassword"] != attpassword)
                    and state["userid"] != post["authorid"]
                    and userconfig["role"] != admin
                ):
                    attpwd = st.text_input(
                        "专属密码",
                        placeholder="请输入专属密码以查看附件内容！",
                        type="password",
                    )
                    if st.button("检查", type="primary"):
                        state["attpassword"] = sha256(attpwd)
                        if sha256(attpwd) == attpassword:
                            st.toast("验证成功！")
                            st.rerun()
                else:
                    # 附件文件只读取一次，避免多次重复读盘
                    files = {}
                    missing = set()
                    for att in attachments:
                        saved_name = att.get("saved_name", "")
                        if not saved_name:
                            continue
                        data = Attachment.get_file(saved_name)
                        if data:
                            files[saved_name] = data
                        else:
                            missing.add(saved_name)

                    for att in attachments:
                        saved_name = att.get("saved_name", "")
                        if saved_name in missing:
                            st.warning(
                                f"附件 {att.get('original_name', '未命名')} 文件已丢失"
                            )
                        elif saved_name and att.get("type", "").startswith("image/"):
                            b64 = base64.b64encode(files[saved_name]).decode()
                            st.image(f"data:{att['type']};base64,{b64}")  # NOQA
                    st.caption(":material/attach_file: 附件文件")

                    zip_buffer = io.BytesIO()
                    with ZipFile(zip_buffer, "w") as zf:
                        for att in attachments:
                            data = files.get(att.get("saved_name", ""))
                            if data:
                                zf.writestr(att.get("original_name", "download"), data)
                    if missing:
                        st.warning(f"有 {len(missing)} 个附件文件已丢失，无法打包")
                    st.download_button(
                        label=":material/folder_zip: 下载所有附件 (ZIP)",
                        data=zip_buffer.getvalue(),
                        file_name=f"{post['title']}-{post['authorid']}.zip",
                        mime="application/zip",
                        key=f"dl_all_{post['id']}",
                        type="primary",
                    )

                    for att in attachments:
                        col_a, col_b, col_c, col_d = st.columns(
                            [0.1, 0.5, 0.2, 0.2], vertical_alignment="center"
                        )
                        saved_name = att.get("saved_name", "")
                        file_bytes = files.get(saved_name, b"")

                        preview(att, col_a, saved_name, attpassword, auto_preview=False)

                        col_b.write(f"**{att.get('original_name', '未命名')}**")
                        col_c.write(f"{format_size(att.get('size', 0))}")
                        if saved_name in missing:
                            col_c.write(":material/warning: 文件已丢失")
                        with col_d.popover("..."):
                            if file_bytes:
                                if st.toggle(
                                    f"预览 “{att.get('original_name', '文件')}”",
                                    key=f"preview_{post['id']}_{saved_name}",
                                ):  # NOQA
                                    b64 = base64.b64encode(file_bytes).decode()
                                    if att.get("type", "").startswith("image/"):
                                        st.image(
                                            f"data:{att['type']};base64,{b64}"
                                        )  # NOQA
                                    elif att.get("type", "").startswith("video/"):
                                        st.video(
                                            f"data:{att['type']};base64,{b64}"
                                        )  # NOQA
                                    elif att.get("type", "").startswith("audio/"):
                                        st.audio(
                                            f"data:{att['type']};base64,{b64}"
                                        )  # NOQA

                                st.download_button(
                                    label=f"下载 “{att.get('original_name', '文件')}”",
                                    data=file_bytes,
                                    file_name=att.get("original_name", "download"),
                                    mime=att.get("type") or "application/octet-stream",
                                    key=f"dl_{post['id']}_{saved_name}",
                                    type="primary",
                                )

            st.divider()
            st.caption(":material/comment: 发表评论")
            if not state.get("userid"):
                st.caption("登录后即可发表评论。")
            col_3, col_4 = st.columns([0.8, 0.2])
            new_comment = col_3.text_area(
                "评论",
                placeholder="良言一句三冬暖，恶语伤人六月寒。支持 Markdown，可用 @用户名 提及他人",
                label_visibility="collapsed",
            )
            col_3.caption("支持 Markdown 排版 · @用户名 可提及他人")
            if col_4.button(":material/send: 提交评论", type="primary"):
                if not state.get("userid"):
                    st.warning("请先登录以提交评论！")
                else:
                    if new_comment:
                        Post.add_comment(int(post_id), state.get("userid"), new_comment)
                        st.toast("评论提交成功")
                        st.rerun()
                    else:
                        st.warning("请输入评论内容！")

            # 评论展示
            if post.get("comments"):
                comments = post["comments"]
                with st.expander(
                    f":material/comment: 评论 ({len(comments)})", expanded=True
                ):
                    # 批量查询评论作者配置，避免逐条 N+1
                    comment_configs = User.get_configs(
                        json.loads(c)["userid"] for c in comments
                    )
                    for index, comment in enumerate(comments):
                        comment = json.loads(comment)
                        cfg = comment_configs.get(comment["userid"])
                        if cfg:
                            col_a, col_b, col_c = st.columns(
                                [0.08, 0.82, 0.1], vertical_alignment="top"
                            )
                            avatar_link(col_a, cfg["avatar"], comment["userid"], 40)
                            col_b.markdown(
                                f":blue-badge[{index + 1}楼] **{cfg['username']}**"
                            )
                            col_b.caption(comment.get("created_at", ""))
                            col_b.markdown(highlight_mentions(comment["content"]))
                            if check_by_state():
                                menu_options = []
                                can_manage = (
                                    userconfig.get("userid") == comment["userid"]
                                    or userconfig.get("role") == admin
                                )
                                if can_manage:
                                    menu_options.append(":material/edit: 编辑")
                                    menu_options.append(":material/delete: 删除")
                                elif userconfig.get("userid") != comment["userid"]:
                                    menu_options.append(":material/report: 举报")
                                if menu_options:
                                    action = col_c.menu_button(
                                        "",
                                        options=menu_options,
                                        icon=":material/more_vert:",
                                        key=f"comment{index}.menu",
                                        type="tertiary",
                                    )
                                    match action:
                                        case ":material/edit: 编辑":
                                            edit_comment_dialog(
                                                post["id"], index, comment["content"]
                                            )
                                        case ":material/delete: 删除":
                                            if Post.delete_comment(int(post_id), index):
                                                st.toast("评论删除成功！")
                                                st.rerun()
                                            else:
                                                st.error("评论删除失败！")
                                        case ":material/report: 举报":
                                            report_comment_dialog(post["id"], comment)
        else:
            st.warning("文章不存在或已被删除！")
    # endregion
    # region 主页显示
    else:
        render_video_background(
            "/app/static/background_720p.mp4",
            blur="5px",
            opacity=0.3,
            backdrop_light="#FFFFFF",
            backdrop_dark="#0E0E0E",
        )
        st.markdown("### :material/forum: 欢迎来到 FreeLake")
        st.caption("分享趣事、校园生活与开发心得的自由论坛——写下你的第一篇帖子吧。")

        # region 快捷入口
        if check_by_state():
            # 全部使用 st.page_link（客户端导航）：与服务端导航（switch_page）
            # 不同，不会触发 Cookie 组件重载导致卡在「正在加载 Cookies」；
            # 且链接均指向不同页面，不会出现当前页高亮背景
            with st.container(horizontal=True, border=True):
                st.page_link(
                    "pages/publish.py",
                    label=":material/edit_note: 发布文章",
                )
                st.page_link(
                    "pages/bookmarks.py",
                    label=":material/bookmark: 我的收藏",
                )
                st.page_link(
                    "pages/home.py",
                    label=":material/settings: 个人设置",
                    query_params={"user_id": state.get("userid", "")},
                )
                current_cfg = user.get_config(state["userid"]) or {}
                if current_cfg.get("role") == admin:
                    st.page_link(
                        "pages/admin.py",
                        label=":material/security: 管理后台",
                    )
        else:
            st.info("登录后可发布文章、参与评论与收藏。", icon=":material/login:")
            with st.container(horizontal=True):
                st.page_link("pages/login.py", label=":material/login: 登录")
                st.page_link("pages/register.py", label=":material/person_add: 注册")
        # endregion

        # region 筛选
        with st.container(border=True):
            search_keyword = st.text_input(
                "搜索文章",
                placeholder="搜索标题或内容",
                label_visibility="collapsed",
                icon=":material/search:",
                value=params.get("search_keyword", ""),
            )
            if search_keyword != params.get("search_keyword", ""):
                if search_keyword:
                    params["search_keyword"] = search_keyword
                else:
                    del params["search_keyword"]
                st.rerun()

            filter_author = st.text_input(
                "按作者查找",
                placeholder="输入作者用户名或用户ID",
                label_visibility="collapsed",
                icon=":material/person:",
                value=params.get("filter_author", ""),
            )
            if filter_author != params.get("filter_author", ""):
                if filter_author:
                    params["filter_author"] = filter_author
                else:
                    del params["filter_author"]
                st.rerun()

            selected_tag = st.pills(":material/filter_alt: 筛选", tags)
            st.caption(":material/info: 支持组合搜索：关键词 + 作者 + 标签 + 日期范围")

            # —— 排序（menu_button：点击展开排序项，选中后按钮文字同步变化）——
            sort_order = st.session_state.get("sort_order", DEFAULT_SORT)
            selected_sort = st.menu_button(
                sort_order,
                options=SORT_OPTIONS,
                icon=":material/sort:",
                key="home_sort_menu",
                type="secondary",
            )
            if selected_sort and selected_sort != sort_order:
                st.session_state["sort_order"] = selected_sort
                st.rerun()
            sort_by = SORT_KEYS.get(sort_order, "date")

            # —— 发布日期范围（默认：起点 2026-08-01，终点为今天；始终生效）——
            today = datetime.date.today()
            date_sel = st.date_input(
                ":material/calendar_month: 发布日期范围",
                value=(DEFAULT_DATE_START, today),
                min_value=datetime.date(2020, 1, 1),
                max_value=today,
                format="YYYY-MM-DD",
                key="home_date_range",
            )
            if date_sel and len(date_sel) == 2:
                date_start, date_end = date_sel[0], date_sel[1]
            else:
                # 清空后回退到默认范围
                date_start, date_end = DEFAULT_DATE_START, today
        # endregion

        # region 分页
        # 关键词 / 标签 / 发布时间统一在 SQL 层过滤，总数与列表口径一致
        total = Post.count_filtered(
            keyword=search_keyword or "",
            tag=selected_tag,
            author=filter_author or "",
            start_date=date_start,
            end_date=date_end,
        )

        with st.bottom:
            page = st.pagination(
                num_pages=max((total + 5 - 1) // 5, 1),
                max_visible_pages=5,
                key="interactive_pagination",
            )
        # endregion

        # 显示文章列表
        if total == 0:
            st.info("没有找到相关文章。", icon=":material/search_off:")
        else:
            posts = Post.get_filtered_paginate(
                keyword=search_keyword or "",
                tag=selected_tag,
                author=filter_author or "",
                page=page,
                page_size=5,
                start_date=date_start,
                end_date=date_end,
                sort_by=sort_by,
            )

            if not posts:
                st.info("没有找到相关文章。", icon=":material/search_off:")
            else:
                # 批量查询作者配置与每篇最新评论，避免 N+1
                author_configs = User.get_configs(p["authorid"] for p in posts)
                last_comments = Post.get_last_comments(posts)

                for post in posts:
                    with st.container(border=True):
                        basic_information(
                            post, author_configs.get(post["authorid"]), compact=True
                        )
                        snippet = (post["content"] or "").strip()
                        if len(snippet) > 150:
                            snippet = snippet[:150] + "…"
                        if snippet:
                            st.markdown(
                                highlight_keyword(snippet, search_keyword or "")
                            )

                        with st.container(horizontal_alignment="right"):
                            if st.button(
                                "阅读全文",
                                icon=":material/arrow_forward:",
                                key=f"view_{post['id']}",
                            ):
                                params["post_id"] = str(post["id"])
                                st.rerun()

                        last = last_comments.get(post["id"])
                        if last:
                            with st.expander(
                                ":material/chat: 最新一条评论", expanded=True
                            ):
                                last_user = author_configs.get(last["userid"])
                                name = (
                                    last_user["username"] if last_user else "用户已注销"
                                )
                                st.markdown(
                                    f"**{name}**：{highlight_mentions(last['content'])} （{last['created_at']}）"
                                )

                        attachments = post.get("attachments", [])
                        if attachments:
                            st.divider()
                            st.caption(":material/attach_file: 附件")
                            attpassword = post.get("attpassword")
                            files = {
                                att.get("saved_name"): Attachment.get_file(
                                    att.get("saved_name")
                                )
                                for att in attachments
                                if att.get("saved_name")
                            }

                            for att in attachments:
                                col_a, col_b, col_c = st.columns([0.1, 0.6, 0.3])
                                saved_name = att.get("saved_name", "")
                                preview(att, col_a, saved_name, attpassword)
                                col_b.write(f"**{att.get('original_name', '未命名')}**")
                                col_c.write(f"{format_size(att.get('size', 0))}")
    # endregion
