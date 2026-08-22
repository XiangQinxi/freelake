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
    Like,
    Post,
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
    assert Bookmark.toggle(pid, u["userid"]) is False


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
