"""
FreeLake 数据层测试（tests/test_data_layer.py）
==============================================

覆盖：注册 / 登录 / 密码升级 / 改密校验 / 文章 CRUD（自增 ID）/ 评论
（增删改 + 时间格式）/ 点赞 / 收藏 / 组合筛选（关键词+标签+收藏）/
作者文章列表 / 浏览量 / 附件（含目录穿越拦截）/ 只读 SQL 守卫。

所有用例使用唯一 userid，互不干扰；数据库为 conftest 提供的内存库。
"""
import io
import re
import uuid

import pytest
from PIL import Image

from conftest import FakeUpload

from api import (
    Attachment,
    Bookmark,
    Draft,
    Like,
    Post,
    Report,
    User,
    execute_sql,
    hash_password,
    sha256,
    verify_password,
)

user_api = User()


def uid(prefix: str = "u") -> str:
    """生成唯一用户 ID。"""
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def make_user(prefix: str = "u") -> dict:
    """注册一个用户并返回凭据。"""
    u = {"userid": uid(prefix), "username": "测试用户", "password": "pass1234"}
    assert user_api.register(u["userid"], u["username"], u["password"]) is True
    return u


# ---------- 注册 / 登录 / 密码 ----------


def test_register_and_login():
    u = make_user()
    assert user_api.exists(u["userid"])
    assert user_api.login(u["userid"], u["password"]) is True
    assert user_api.login(u["userid"], "wrongpass") is False
    assert user_api.login(u["userid"], None) is False


def test_register_duplicate_fails():
    u = make_user()
    assert user_api.register(u["userid"], "另一个", "pass1234") is False


def test_old_sha256_password_auto_upgrade():
    """旧版 sha256 密码登录后自动升级为 PBKDF2。"""
    from api import _User

    userid = uid("legacy")
    _User.create(
        userid=userid,
        username="旧用户",
        password=sha256("legacy123"),
        role="USER",
        created_at="2026-01-01 00:00:00",
    )
    assert user_api.login(userid, "legacy123") is True
    stored = _User.get(_User.userid == userid).password
    assert stored.startswith("pbkdf2$")
    assert verify_password("legacy123", stored) is True


def test_hash_verify_roundtrip():
    h = hash_password("s3cret")
    assert h.startswith("pbkdf2$")
    assert verify_password("s3cret", h) is True
    assert verify_password("wrong", h) is False
    # 兼容旧 sha256 格式
    assert verify_password("abc", sha256("abc")) is True


# ---------- 改密（B1） ----------


def test_change_password_requires_original():
    """B1 回归：原密码错误时 modify 必须拒绝，且密码不被改动。"""
    u = make_user()
    assert user_api.check(u["userid"], u["password"]) is True
    assert user_api.check(u["userid"], "wrong-original") is False
    # 原密码错误 → 拒绝，密码保持原样
    assert (
        user_api.modify(
            u["userid"], password="wrong-original", new_password="newpass123"
        )
        is False
    )
    assert user_api.login(u["userid"], u["password"]) is True
    assert user_api.login(u["userid"], "newpass123") is False
    # 原密码正确 → 允许
    assert (
        user_api.modify(
            u["userid"], password=u["password"], new_password="newpass123"
        )
        is True
    )
    assert user_api.login(u["userid"], "newpass123") is True


# ---------- 文章 / 评论（B5 / B6 / B3 / F6） ----------


def test_post_publish_auto_increment_id():
    """B5 回归：ID 由数据库自增，连续发布不会重复。"""
    u = make_user()
    id1 = Post.publish(u["userid"], "标题一", "内容一", [])
    id2 = Post.publish(u["userid"], "标题二", "内容二", [])
    assert isinstance(id1, int) and isinstance(id2, int)
    assert id1 != id2
    p1 = Post.get(id1)
    assert p1["title"] == "标题一"
    assert p1["views"] == 0


def test_post_edit_and_delete():
    u = make_user()
    pid = Post.publish(u["userid"], "原标题", "原内容", [])
    assert Post.edit(pid, "新标题", "新内容", ["趣事分享"]) is True
    p = Post.get(pid)
    assert p["title"] == "新标题"
    assert p["tags"] == ["趣事分享"]
    assert Post.delete(pid) is True
    assert Post.get(pid) is None


def test_comment_add_edit_delete():
    """评论增删改；B6 时间格式统一；B3 编辑按索引定位。"""
    from api import _Comment

    u = make_user()
    pid = Post.publish(u["userid"], "有评论", "内容", [])
    cid = Post.add_comment(pid, u["userid"], "第一条评论")
    assert cid is not None
    p = Post.get(pid)
    assert len(p["comments"]) == 1
    assert "第一条评论" in p["comments"][0]

    # B6：评论时间使用与文章一致的 YYYY-MM-DD HH:MM:SS
    row = _Comment.get(_Comment.id == cid)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", row.created_at)

    # B3：编辑评论
    assert Post.edit_comment(pid, 0, "修改后的评论") is True
    assert "修改后的评论" in Post.get(pid)["comments"][0]
    assert Post.edit_comment(pid, 99, "越界索引") is False

    # 按索引删除评论
    assert Post.delete_comment(pid, 0) is True
    assert Post.get(pid)["comments"] == []


def test_like_and_bookmark_toggle():
    u = make_user()
    pid = Post.publish(u["userid"], "互动", "内容", [])
    # 点赞
    assert Like.toggle(pid, u["userid"]) is True
    assert Like.is_liked(pid, u["userid"]) is True
    assert Like.count(pid) == 1
    assert Like.toggle(pid, u["userid"]) is False
    assert Like.count(pid) == 0
    # 收藏
    assert Bookmark.toggle(pid, u["userid"]) is True
    assert Bookmark.is_bookmarked(pid, u["userid"]) is True
    assert Bookmark.count(pid) == 1
    assert Bookmark.toggle(pid, u["userid"]) is False
    assert Bookmark.count(pid) == 0


def test_views_increment():
    """F6：浏览量累加。"""
    u = make_user()
    pid = Post.publish(u["userid"], "浏览量", "内容", [])
    assert Post.get(pid)["views"] == 0
    Post.add_view(pid)
    Post.add_view(pid)
    assert Post.get(pid)["views"] == 2


# ---------- 组合筛选（B2 / F3 / F4） ----------


def test_count_filtered_and_paginate_consistent():
    """B2 回归：关键词/标签/收藏在 SQL 层过滤，计数与列表口径一致。"""
    a = make_user("a")
    b = make_user("b")
    p1 = Post.publish(a["userid"], "趣事：今天天气好", "内容A", [], tags=["趣事分享"])
    p2 = Post.publish(a["userid"], "寻物启事", "内容B", [], tags=["寻物招领"])
    p3 = Post.publish(b["userid"], "社团招新", "内容C", [], tags=["社团公告"])
    p4 = Post.publish(b["userid"], "趣事：食堂新品", "内容D", [], tags=["趣事分享"])

    # 全量
    assert Post.count_filtered() == 4
    assert len(Post.get_filtered_paginate(page=1, page_size=10)) == 4

    # 关键词（标题/内容）
    assert Post.count_filtered(keyword="天气") == 1
    assert Post.get_filtered_paginate(keyword="天气")[0]["id"] == p1

    # 标签（B2：SQL 层精确匹配，分页总数与列表一致）
    assert Post.count_filtered(tag="趣事分享") == 2
    assert {p["id"] for p in Post.get_filtered_paginate(tag="趣事分享")} == {p1, p4}
    assert Post.count_filtered(tag="寻物招领") == 1
    assert Post.get_filtered_paginate(tag="寻物招领")[0]["id"] == p2
    assert Post.count_filtered(tag="不存在的标签") == 0

    # 收藏（F4）
    Bookmark.toggle(p3, a["userid"])
    Bookmark.toggle(p4, a["userid"])
    bm_ids = Bookmark.get_bookmarked_post_ids(a["userid"])
    assert Post.count_filtered(post_ids=bm_ids) == 2
    assert {p["id"] for p in Post.get_filtered_paginate(post_ids=bm_ids)} == {p3, p4}

    # 组合：收藏 + 标签
    assert Post.count_filtered(tag="趣事分享", post_ids=bm_ids) == 1
    assert Post.get_filtered_paginate(tag="趣事分享", post_ids=bm_ids)[0]["id"] == p4

    # 分页与总数一致：第 2 页为空数据量推算正确
    assert len(Post.get_filtered_paginate(tag="趣事分享", page=1, page_size=1)) == 1
    assert len(Post.get_filtered_paginate(tag="趣事分享", page=2, page_size=1)) == 1


def test_count_filtered_by_date_range():
    """发布时间范围过滤：SQL 层生效，计数与列表口径一致（起止日期均含）。"""
    import datetime

    import api

    u = make_user()
    p1 = Post.publish(u["userid"], "一月发布", "内容A", [])
    p2 = Post.publish(u["userid"], "二月发布", "内容B", [])
    p3 = Post.publish(u["userid"], "三月发布", "内容C", [])

    # 人为指定 created_at 日期（默认均为当前时间）
    for pid, ts in {
        p1: "2024-01-15 10:00:00",
        p2: "2024-02-20 10:00:00",
        p3: "2024-03-10 10:00:00",
    }.items():
        api._Post.update(created_at=ts).where(api._Post.id == pid).execute()

    # 起止日期均含：2 月 1 日 ~ 3 月 31 日 → p2 / p3
    start = datetime.date(2024, 2, 1)
    end = datetime.date(2024, 3, 31)
    assert Post.count_filtered(start_date=start, end_date=end) == 2
    ids = {
        p["id"]
        for p in Post.get_filtered_paginate(start_date=start, end_date=end)
    }
    assert ids == {p2, p3}

    # 只看起始日期（>= start）
    assert Post.count_filtered(start_date=start) == 2

    # 只看结束日期（<= end）
    assert Post.count_filtered(end_date=datetime.date(2024, 1, 31)) == 1

    # 区间不命中
    assert Post.count_filtered(start_date=datetime.date(2025, 1, 1)) == 0


def test_get_filtered_paginate_sort():
    """排序：按日期 / 浏览量 / 点赞量 / 收藏量降序。"""
    import api

    a = make_user("a")
    b = make_user("b")
    p1 = Post.publish(a["userid"], "文章一", "内容", [])
    p2 = Post.publish(b["userid"], "文章二", "内容", [])
    p3 = Post.publish(a["userid"], "文章三", "内容", [])

    # 浏览量
    api._Post.update(views=10).where(api._Post.id == p1).execute()
    api._Post.update(views=30).where(api._Post.id == p2).execute()
    api._Post.update(views=20).where(api._Post.id == p3).execute()
    views_sorted = [
        p["id"] for p in Post.get_filtered_paginate(sort_by="views", page_size=10)
    ]
    assert views_sorted == [p2, p3, p1]

    # 日期（默认）：最新在前
    date_sorted = [
        p["id"] for p in Post.get_filtered_paginate(sort_by="date", page_size=10)
    ]
    assert date_sorted == sorted([p1, p2, p3], reverse=True)

    # 点赞量：p3=2 > p2=1 > p1=0
    Like.toggle(p2, a["userid"])
    Like.toggle(p3, a["userid"])
    Like.toggle(p3, b["userid"])
    likes_sorted = [
        p["id"] for p in Post.get_filtered_paginate(sort_by="likes", page_size=10)
    ]
    assert likes_sorted == [p3, p2, p1]

    # 收藏量：p1=1 排在其余（0）之前
    Bookmark.toggle(p1, a["userid"])
    bookmarks_sorted = [
        p["id"]
        for p in Post.get_filtered_paginate(sort_by="bookmarks", page_size=10)
    ]
    assert bookmarks_sorted[0] == p1


def test_get_by_author():
    """F3：个人主页文章列表。"""
    u = make_user()
    ids = [Post.publish(u["userid"], f"文章{i}", "内容", []) for i in range(3)]
    Post.publish("someoneelse", "别人的", "内容", [])
    got = Post.get_by_author(u["userid"])
    assert len(got) == 3
    assert all(p["authorid"] == u["userid"] for p in got)
    assert {p["id"] for p in got} == set(ids)
    # 倒序（最新在前）
    assert got[0]["id"] > got[-1]["id"]


# ---------- 附件（S2） ----------


def test_attachment_save_get_and_traversal_blocked(tmp_dirs):
    data = b"hello attachment"
    meta = Attachment.save(FakeUpload("a.txt", data, "text/plain"))
    # 保存名应为 uuid 生成（不是原始文件名）
    assert re.fullmatch(r"[0-9a-f]{32}\.txt", meta["saved_name"])
    assert meta["saved_name"] != "a.txt"
    assert Attachment.get_file(meta["saved_name"]) == data
    # S2：目录穿越被拦截（basename 后落到附件目录内，找不到返回空）
    assert Attachment.get_file("../../.streamlit/secrets.toml") == b""
    assert Attachment.get_file("../data.db") == b""


def test_attachment_thumbnail(tmp_dirs):
    # 生成一张 400x300 PNG
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "red").save(buf, "PNG")
    meta = Attachment.save(FakeUpload("pic.png", buf.getvalue(), "image/png"))
    thumb = Attachment.get_thumbnail_bytes(meta["saved_name"], max_width=100)
    assert thumb.startswith(b"\xff\xd8")  # JPEG
    assert thumb != buf.getvalue()


# ---------- 头像压缩 ----------


def test_avatar_compression(tmp_dirs):
    """高清头像上传后：1:1 裁切、缩小到 512 上限、转 JPEG、体积显著减小。"""
    from api import MAX_AVATAR_DIM, get_avatar_bytes, save_avatar

    # 生成 2000x3000 的 RGBA 大图（带透明区域），模拟高清 PNG 头像
    original = Image.new("RGBA", (2000, 3000), (30, 144, 255, 255))
    for x in range(0, 2000, 10):
        for y in range(0, 3000, 10):
            original.putpixel((x, y), (0, 0, 0, 0))
    buf = io.BytesIO()
    original.save(buf, "PNG")
    raw = buf.getvalue()

    meta = save_avatar(FakeUpload("avatar.png", raw, "image/png"))
    # 压缩后统一为 .jpg，且体积明显小于原始 PNG
    assert meta["saved_name"].endswith(".jpg")
    assert meta["type"] == "image/jpeg"
    assert meta["size"] < len(raw) / 2

    # 读回并校验：JPEG 且边长不超过上限（1:1 裁切后为正方形）
    data = get_avatar_bytes(meta["saved_name"])
    assert data.startswith(b"\xff\xd8")
    img = Image.open(io.BytesIO(data))
    assert img.width == img.height
    assert img.width <= MAX_AVATAR_DIM


# ---------- 举报（B1） ----------


def test_report_add_and_deduplicate():
    u = make_user()
    u2 = make_user("r")
    pid = Post.publish(u["userid"], "被举报", "内容", [])
    # 首次举报成功
    rid = Report.add(
        u2["userid"],
        postid=pid,
        target_userid=u["userid"],
        content_preview="被举报",
        reason="广告",
    )
    assert rid is not None
    # 相同举报人 + 同一目标 + pending → 不重复提交
    assert (
        Report.add(
            u2["userid"],
            postid=pid,
            target_userid=u["userid"],
            content_preview="被举报",
            reason="广告",
        )
        is None
    )
    # 不同举报人可再次举报
    assert (
        Report.add(
            u["userid"],
            postid=pid,
            target_userid=u["userid"],
            content_preview="被举报",
            reason="违禁",
        )
        is not None
    )
    assert Report.count("pending") == 2


def test_report_handle_and_status():
    u = make_user()
    u2 = make_user("r")
    pid = Post.publish(u["userid"], "t", "c", [])
    rid = Report.add(
        u2["userid"], postid=pid, target_userid=u["userid"], content_preview="t", reason="x"
    )
    assert Report.handle(rid, "admin", "handled", "已删除") is True
    r = Report.get(rid)
    assert r["status"] == "handled"
    assert r["handled_by"] == "admin"
    # 非法 action 拒绝
    assert Report.handle(rid, "admin", "weird") is False


def test_report_comment():
    u = make_user()
    u2 = make_user("r")
    pid = Post.publish(u["userid"], "t", "c", [])
    cid = Post.add_comment(pid, u["userid"], "评论内容")
    rid = Report.add(
        u2["userid"],
        postid=pid,
        commentid=cid,
        target_userid=u["userid"],
        content_preview="评论内容",
        reason="辱骂",
    )
    assert rid is not None
    r = Report.get(rid)
    assert r["commentid"] == cid
    assert r["postid"] == pid


# ---------- 作者筛选 / 作者统计（B4 / B5） ----------


def test_count_filtered_by_author():
    a = make_user("author")
    b = make_user("writer")
    p1 = Post.publish(a["userid"], "A 的文章一", "内容", [])
    p2 = Post.publish(a["userid"], "A 的文章二", "内容", [])
    p3 = Post.publish(b["userid"], "B 的文章", "内容", [])

    # 按 userid 精确模糊（contains）
    assert Post.count_filtered(author=a["userid"]) == 2
    assert Post.count_filtered(author=a["userid"][:5]) == 2
    # 无匹配作者 → 0
    assert Post.count_filtered(author="不存在的人") == 0
    # 与关键词组合
    assert Post.count_filtered(keyword="文章一", author=a["userid"]) == 1
    assert {p["id"] for p in Post.get_filtered_paginate(author=a["userid"])} == {p1, p2}


def test_get_author_stats():
    import api

    a = make_user("hero")
    b = make_user("fan")
    pid = Post.publish(a["userid"], "标题", "内容", [])
    p2 = Post.publish(a["userid"], "标题二", "内容", [])
    api._Post.update(views=10).where(api._Post.id == pid).execute()
    Like.toggle(pid, b["userid"])
    Bookmark.toggle(pid, b["userid"])

    stats = Post.get_author_stats(a["userid"])
    assert stats["post_count"] == 2
    assert stats["views"] == 10
    assert stats["likes"] == 1
    assert stats["bookmarks"] == 1
    # 其他用户无文章
    empty = Post.get_author_stats(b["userid"])
    assert empty["post_count"] == 0


# ---------- 草稿（B6） ----------


def test_draft_save_load_delete():
    u = make_user()
    did = Draft.save(u["userid"], "草稿标题", "草稿内容", ["趣事分享"], "pw123")
    assert did is not None
    assert Draft.count(u["userid"]) == 1

    d = Draft.get_draft(u["userid"], did)
    assert d["title"] == "草稿标题"
    assert d["tags"] == ["趣事分享"]

    # 更新已有草稿
    d2 = Draft.save(
        u["userid"], "新标题", "新内容", ["技术分享"], "pw456", draft_id=did
    )
    assert d2 == did
    assert Draft.get_draft(u["userid"], did)["title"] == "新标题"

    # 仅本人可读/删
    other = make_user("other")
    assert Draft.get_draft(other["userid"], did) is None
    assert Draft.delete(other["userid"], did) is False
    assert Draft.delete(u["userid"], did) is True
    assert Draft.count(u["userid"]) == 0


def test_report_post_and_comment_are_distinct():
    u = make_user()
    u2 = make_user("r")
    pid = Post.publish(u["userid"], "t", "c", [])
    cid = Post.add_comment(pid, u["userid"], "评论")
    # 举报帖子与举报该帖下的评论是不同目标，互不误判为重复
    assert (
        Report.add(
            u2["userid"], postid=pid, target_userid=u["userid"], content_preview="t", reason="帖子"
        )
        is not None
    )
    assert (
        Report.add(
            u2["userid"],
            postid=pid,
            commentid=cid,
            target_userid=u["userid"],
            content_preview="评论",
            reason="评论",
        )
        is not None
    )
    # 同一评论重复举报 → 去重
    assert (
        Report.add(
            u2["userid"],
            postid=pid,
            commentid=cid,
            target_userid=u["userid"],
            content_preview="评论",
            reason="再来",
        )
        is None
    )


# ---------- 只读 SQL 守卫（S3） ----------


def test_execute_sql_readonly():
    """S3：只允许 SELECT / WITH / EXPLAIN / PRAGMA，写操作一律拒绝。"""
    assert execute_sql("SELECT 1 AS x") == [{"x": 1}]
    # 大小写 / 前后空白 / 分号均可
    assert execute_sql("  select count(*) as n from _user  ;") is not None
    for bad in (
        "DELETE FROM _user",
        "UPDATE _user SET role='ADMIN'",
        "DROP TABLE _user",
        "INSERT INTO _user (userid) VALUES ('x')",
        "",
    ):
        with pytest.raises(ValueError):
            execute_sql(bad)
