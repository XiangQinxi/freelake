"""
文章与评论（freelake/services/posts.py）
=======================================

发布 / 编辑 / 删除文章，标签与附件明细同步，评论按「文章内楼层次序」增删改，
以及列表筛选、分页、作者聚合。对外 dict 由 `db.adapters` 组装，键名保持 v1。
"""

import datetime
import logging
import typing

from peewee import fn

from freelake.db.adapters import _last_comment_map, _post_dicts
from freelake.db.connection import db
from freelake.db.models import (
    AttachmentModel as _Attachment,
)
from freelake.db.models import (
    BookmarkModel as _Bookmark,
)
from freelake.db.models import (
    CommentModel as _Comment,
)
from freelake.db.models import (
    LikeModel as _Like,
)
from freelake.db.models import (
    PostModel as _Post,
)
from freelake.db.models import (
    PostTagModel as _PostTag,
)
from freelake.db.models import (
    TagModel as _Tag,
)
from freelake.db.models import (
    UserModel as _User,
)
from freelake.db.models import (
    now_str,
)
from freelake.services.security import sha256

logger = logging.getLogger(__name__)


def _sync_tags(post_id: int, tags: typing.Iterable[str] | None) -> None:
    """用给定标签集合整体替换文章标签（保持用户选择顺序，自动去重）。"""
    names: list[str] = []
    for name in tags or []:
        name = (name or "").strip()
        if name and name not in names:
            names.append(name)
    with db.atomic():
        old_ids = [
            row["tag"]
            for row in _PostTag.select(_PostTag.tag).where(_PostTag.post == post_id).dicts()
        ]
        _PostTag.delete().where(_PostTag.post == post_id).execute()
        # 标签计数：旧集合 -1，新集合 +1（同一事务内完成）
        for tag_id in old_ids:
            _Tag.update(usage_count=_Tag.usage_count - 1).where(_Tag.id == tag_id).execute()
        for position, name in enumerate(names):
            tag, _created = _Tag.get_or_create(name=name)
            _Tag.update(usage_count=_Tag.usage_count + 1).where(_Tag.id == tag.id).execute()
            _PostTag.create(post=post_id, tag=tag, position=position)


def _sync_attachments(post_id: int, attachments: typing.Iterable[dict]) -> None:
    """把发布时上传的附件元数据写入 _attachment（文件本体已由 Attachment.save 落盘）。"""
    for att in attachments or []:
        stored_name = att.get("saved_name")
        if not stored_name:
            continue
        _Attachment.create(
            post=post_id,
            original_name=att.get("original_name") or stored_name,
            stored_name=stored_name,
            mime=att.get("type") or "",
            size=int(att.get("size") or 0),
            sha256=att.get("sha256") or "",
            is_image=1 if (att.get("type") or "").startswith("image/") else 0,
            created_at=now_str(),
        )


def _comment_id_at(postid: int, comment_index: int) -> int | None:
    """按「文章内楼层次序」取评论 ID（与 _comments_map 的排序一致）。"""
    if comment_index < 0:
        return None
    return (
        _Comment.select(_Comment.id)
        .where(_Comment.post == postid)
        .order_by(_Comment.created_at, _Comment.id)
        .offset(comment_index)
        .limit(1)
        .scalar()
    )


class Post:
    """文章接口。所有返回值保持 v1 键名（authorid / tags / attachments / comments）。"""

    # region 写操作
    @staticmethod
    def publish(
        authorid: str,
        title: str,
        content: str,
        attachments: list[dict[str, str]],
        attpassword: str | None = None,
        tags: list[str] = None,
    ) -> int:
        """发布文章，返回新文章 ID（附件与标签在同一事务内写入）。"""
        author = _User.get_or_none(_User.userid == authorid)
        with db.atomic():
            post = _Post.create(
                author_id=authorid,
                # 作者名快照：作者注销/改名后，历史文章的署名仍可还原
                author_name=author.username if author else "",
                title=title,
                content=content,
                created_at=now_str(),
                updated_at=now_str(),
                attpassword=sha256(attpassword) if attpassword else "",
            )
            _sync_attachments(post.id, attachments)
            _sync_tags(post.id, tags)
        logger.info("%s 发布了新文章《%s》（post_id=%s）", authorid, title, post.id)
        return post.id

    @staticmethod
    def delete(postid: int) -> bool:
        """删除文章。

        评论 / 点赞 / 收藏 / 标签 / 附件由外键级联清理（v1 需手工删评论，其余会留孤儿）；
        磁盘上的附件文件不在这里删，留给管理后台的「孤立附件清理」。
        """
        with db.atomic():
            tag_ids = [
                row["tag"]
                for row in _PostTag.select(_PostTag.tag).where(_PostTag.post == postid).dicts()
            ]
            for tag_id in tag_ids:
                _Tag.update(usage_count=_Tag.usage_count - 1).where(_Tag.id == tag_id).execute()
            return _Post.delete().where(_Post.id == postid).execute() > 0

    @staticmethod
    def edit(postid: int, title: str, content: str, tags: list[str] = None) -> bool:
        """编辑文章标题 / 正文 / 标签。"""
        with db.atomic():
            updated = (
                _Post.update(title=title, content=content, updated_at=now_str())
                .where(_Post.id == postid)
                .execute()
            )
            if not updated:
                return False
            if tags is not None:
                _sync_tags(postid, tags)
        return True

    @staticmethod
    def add_view(postid: int) -> None:
        """文章浏览量 +1（单条 UPDATE，不必读回整行）。"""
        _Post.update(views=_Post.views + 1).where(_Post.id == postid).execute()

    # endregion

    # region 读操作
    @staticmethod
    def get_all() -> list[dict]:
        """全部文章（管理后台的图表 / 统计用，含标签）。"""
        return _post_dicts(_Post.select().order_by(_Post.id).dicts())

    @staticmethod
    def _apply_filters(
        query,
        keyword: str = "",
        tag: str | None = None,
        post_ids: typing.Iterable[int] | None = None,
        author: str = "",
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
    ):
        """按关键词 / 标签 / 文章 ID 集合 / 作者 / 发布时间范围过滤（SQL 层，计数与分页共用）。

        - 标签：走 _post_tag 关联表等值匹配（v1 是 JSON.contains，无法走索引）
        - 关键词：标题 / 正文模糊匹配
        - 作者：userid 或用户名模糊匹配
        - 发布时间：created_at 字符串区间（字典序即时间序），起止日期均包含
        """
        if keyword:
            query = query.where((_Post.title.contains(keyword)) | (_Post.content.contains(keyword)))
        if tag:
            tagged = (
                _PostTag.select(_PostTag.post)
                .join(_Tag, on=(_PostTag.tag == _Tag.id))
                .where(_Tag.name == tag)
            )
            query = query.where(_Post.id.in_(tagged))
        if post_ids is not None:
            query = query.where(_Post.id.in_(list(post_ids)))
        if author:
            author_ids = [
                u.userid
                for u in _User.select().where(
                    (_User.userid.contains(author)) | (_User.username.contains(author))
                )
            ]
            if author_ids:
                query = query.where(_Post.author_id.in_(author_ids))
            else:
                # 无匹配作者 → 查询恒为空
                query = query.where(_Post.id == -1)
        if start_date is not None:
            query = query.where(_Post.created_at >= start_date.strftime("%Y-%m-%d") + " 00:00:00")
        if end_date is not None:
            query = query.where(_Post.created_at <= end_date.strftime("%Y-%m-%d") + " 23:59:59")
        return query

    @staticmethod
    def count_filtered(
        keyword: str = "",
        tag: str | None = None,
        post_ids: typing.Iterable[int] | None = None,
        author: str = "",
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
    ) -> int:
        """按过滤条件统计文章总数（分页用，与列表口径一致）。"""
        return Post._apply_filters(
            _Post.select(), keyword, tag, post_ids, author, start_date, end_date
        ).count()

    @staticmethod
    def get_filtered_paginate(
        keyword: str = "",
        tag: str | None = None,
        post_ids: typing.Iterable[int] | None = None,
        author: str = "",
        page: int = 1,
        page_size: int = 5,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
        sort_by: str = "date",
    ) -> list[dict]:
        """按条件过滤后分页取文章。

        ``sort_by``：``date``（默认）/ ``views`` / ``likes`` / ``bookmarks``。
        四种排序全部下推到 SQL，且排序键都有索引（v1 的 likes/bookmarks 会先全表
        聚合计数、再把全部文章拉回 Python 排序切片）。
        """
        query = Post._apply_filters(
            _Post.select(), keyword, tag, post_ids, author, start_date, end_date
        )

        if sort_by == "views":
            order_by = (_Post.views.desc(), _Post.id.desc())
        elif sort_by == "likes":
            order_by = (_Post.like_count.desc(), _Post.id.desc())
        elif sort_by == "bookmarks":
            order_by = (_Post.bookmark_count.desc(), _Post.id.desc())
        else:
            order_by = (_Post.created_at.desc(), _Post.id.desc())

        rows = query.order_by(*order_by).paginate(page, page_size).dicts()
        return _post_dicts(rows)

    @staticmethod
    def get_by_author(authorid: str) -> list[dict]:
        """某用户发布的全部文章（发布时间倒序），用于个人主页。"""
        rows = (
            _Post.select()
            .where(_Post.author_id == authorid)
            .order_by(_Post.created_at.desc(), _Post.id.desc())
            .dicts()
        )
        return _post_dicts(rows)

    @staticmethod
    def get_author_stats(authorid: str) -> dict:
        """统计某作者的文章数 / 总浏览量 / 总获赞 / 总获藏（单条聚合查询）。"""
        row = (
            _Post.select(
                fn.COUNT(_Post.id).alias("post_count"),
                fn.COALESCE(fn.SUM(_Post.views), 0).alias("views"),
                fn.COALESCE(fn.SUM(_Post.like_count), 0).alias("likes"),
                fn.COALESCE(fn.SUM(_Post.bookmark_count), 0).alias("bookmarks"),
            )
            .where(_Post.author_id == authorid)
            .dicts()
            .first()
        ) or {}
        return {
            "post_count": row.get("post_count") or 0,
            "views": row.get("views") or 0,
            "likes": row.get("likes") or 0,
            "bookmarks": row.get("bookmarks") or 0,
        }

    @staticmethod
    def get(_id: int) -> dict | None:
        """文章详情（评论为 json 字符串列表，保持 v1 契约）。"""
        row = _Post.select().where(_Post.id == _id).dicts().get_or_none()
        if not row:
            return None
        return _post_dicts([row], with_comments=True)[0]

    @staticmethod
    def get_last_comments(posts: typing.Iterable[dict]) -> dict[int, dict]:
        """批量取每篇文章的最新一条评论（单次查询），返回 {post_id: comment}。"""
        return _last_comment_map([p["id"] for p in posts if p.get("id")])

    @staticmethod
    def count() -> int:
        """文章总数"""
        return _Post.select().count()

    # endregion

    # region 评论（按「文章内楼层次序」定位，v1 用 JSON 数组下标实现同样的语义）
    @staticmethod
    def add_comment(postid: int, userid: str, content: str) -> int | None:
        """添加评论，返回评论 ID；文章不存在时返回 None。"""
        with db.atomic():
            if _Post.get_or_none(_Post.id == postid) is None:
                return None
            author = _User.get_or_none(_User.userid == userid)
            comment = _Comment.create(
                post=postid,
                user_id=userid,
                author_name=author.username if author else "",
                content=content,
                created_at=now_str(),
                updated_at=now_str(),
            )
            _Post.update(comment_count=_Post.comment_count + 1).where(_Post.id == postid).execute()
        return comment.id

    @staticmethod
    def edit_comment(postid: int, comment_index: int, content: str) -> bool:
        """编辑评论（comment_index 为文章内楼层次序，0 起）。"""
        cid = _comment_id_at(postid, comment_index)
        if cid is None:
            return False
        return (
            _Comment.update(content=content, updated_at=now_str())
            .where(_Comment.id == cid)
            .execute()
            > 0
        )

    @staticmethod
    def delete_comment(postid: int, comment_index: int) -> bool:
        """删除评论（comment_index 为文章内楼层次序，0 起）。"""
        cid = _comment_id_at(postid, comment_index)
        if cid is None:
            return False
        with db.atomic():
            deleted = _Comment.delete().where(_Comment.id == cid).execute()
            if deleted:
                _Post.update(comment_count=_Post.comment_count - 1).where(
                    _Post.id == postid
                ).execute()
        return bool(deleted)

    # endregion

    @staticmethod
    def recount(postid: int) -> dict | None:
        """按明细表重算某篇文章的冗余计数器（数据修复 / 迁移校验用）。"""
        post = _Post.get_or_none(_Post.id == postid)
        if post is None:
            return None
        likes = _Like.select().where(_Like.post == postid).count()
        bookmarks = _Bookmark.select().where(_Bookmark.post == postid).count()
        comments = _Comment.select().where(_Comment.post == postid).count()
        _Post.update(like_count=likes, bookmark_count=bookmarks, comment_count=comments).where(
            _Post.id == postid
        ).execute()
        return {"likes": likes, "bookmarks": bookmarks, "comments": comments}
