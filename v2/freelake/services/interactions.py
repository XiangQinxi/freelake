"""
点赞 / 收藏 / 举报（freelake/services/interactions.py）
=====================================================

都靠「明细表 + 冗余计数器」工作：明细唯一键兜底并发重复，计数器在同一事务内
增减，列表排序因此能直接走索引。
"""

from freelake.db.adapters import _report_dict
from freelake.db.connection import UNIQUE_VIOLATION, db
from freelake.db.models import (
    TARGET_COMMENT,
    TARGET_POST,
    now_str,
)
from freelake.db.models import (
    BookmarkModel as _Bookmark,
)
from freelake.db.models import (
    LikeModel as _Like,
)
from freelake.db.models import (
    PostModel as _Post,
)
from freelake.db.models import (
    ReportModel as _Report,
)


class Like:
    """文章点赞：同一用户对同一文章只能点一次（主键 post_id + user_id 保证）。

    _post.like_count 与明细在同一个事务内增减，排序因此可以直接走索引。
    """

    @staticmethod
    def toggle(postid: int, userid: str) -> bool:
        """切换点赞状态，返回点赞后的状态（True = 已点赞）。"""
        try:
            with db.atomic():
                like = _Like.get_or_none((_Like.post == postid) & (_Like.user_id == userid))
                if like:
                    like.delete_instance()
                    _Post.update(like_count=_Post.like_count - 1).where(
                        _Post.id == postid
                    ).execute()
                    return False
                _Like.create(post=postid, user_id=userid, created_at=now_str())
                _Post.update(like_count=_Post.like_count + 1).where(_Post.id == postid).execute()
                return True
        except UNIQUE_VIOLATION:
            # 文章已被删除：外键拒绝写入
            return False

    @staticmethod
    def is_liked(postid: int, userid: str) -> bool:
        """用户是否已点赞该文章。"""
        return _Like.get_or_none((_Like.post == postid) & (_Like.user_id == userid)) is not None

    @staticmethod
    def count(postid: int) -> int:
        """文章的点赞总数（读 _post 冗余计数器，主键点查）。"""
        return _Post.select(_Post.like_count).where(_Post.id == postid).scalar() or 0


class Bookmark:
    """文章收藏：同一用户对同一文章只能收藏一次（主键 post_id + user_id 保证）。"""

    @staticmethod
    def toggle(postid: int, userid: str) -> bool:
        """切换收藏状态，返回收藏后的状态（True = 已收藏）。"""
        try:
            with db.atomic():
                bm = _Bookmark.get_or_none(
                    (_Bookmark.post == postid) & (_Bookmark.user_id == userid)
                )
                if bm:
                    bm.delete_instance()
                    _Post.update(bookmark_count=_Post.bookmark_count - 1).where(
                        _Post.id == postid
                    ).execute()
                    return False
                _Bookmark.create(post=postid, user_id=userid, created_at=now_str())
                _Post.update(bookmark_count=_Post.bookmark_count + 1).where(
                    _Post.id == postid
                ).execute()
                return True
        except UNIQUE_VIOLATION:
            return False

    @staticmethod
    def is_bookmarked(postid: int, userid: str) -> bool:
        """用户是否已收藏该文章。"""
        return (
            _Bookmark.get_or_none((_Bookmark.post == postid) & (_Bookmark.user_id == userid))
            is not None
        )

    @staticmethod
    def count(postid: int) -> int:
        """文章的收藏总数（读 _post 冗余计数器）。"""
        return _Post.select(_Post.bookmark_count).where(_Post.id == postid).scalar() or 0

    @staticmethod
    def get_bookmarked_post_ids(userid: str) -> list[int]:
        """获取用户收藏的所有文章 ID（按收藏时间倒序）。"""
        return [
            row["post"]
            for row in _Bookmark.select(_Bookmark.post)
            .where(_Bookmark.user_id == userid)
            .order_by(_Bookmark.created_at.desc())
            .dicts()
        ]


class Report:
    """举报：用户可举报文章或评论，管理员在后台受理处理。

    「举报文章」与「举报评论」靠 comment_id 是否为 NULL 区分。同一举报人对同一
    目标只允许一条 pending 举报（由 partial unique index 保证，见 init_database）。
    """

    @staticmethod
    def add(
        reporter_userid: str,
        *,
        postid: int | None = None,
        commentid: int | None = None,
        target_userid: str = "",
        content_preview: str = "",
        reason: str = "",
    ) -> int | None:
        """提交举报，返回举报 ID；重复举报（同一人对同一目标且未处理）返回 None。

        参数：
            reporter_userid: 举报人 userid
            postid / commentid: 被举报目标（举报文章传 postid，举报评论传 postid+commentid）
            target_userid: 被举报内容作者 userid（便于后台核对）
            content_preview: 被举报内容摘要快照
            reason: 举报理由（必填）
        """
        if not reporter_userid:
            return None
        reason = (reason or "").strip()
        if not reason:
            return None
        is_comment = commentid is not None
        target_id = int(commentid) if is_comment else int(postid or 0)
        if not target_id:
            return None
        try:
            return _Report.create(
                # 举报对象：评论 / 文章。post_id 只是上下文（评论所属文章）
                target_type=TARGET_COMMENT if is_comment else TARGET_POST,
                target_id=target_id,
                post_id=postid,
                target_user_id=target_userid,
                reporter_id=reporter_userid,
                content_preview=(content_preview or "")[:500],
                reason=reason,
                status="pending",
                created_at=now_str(),
            ).id
        except UNIQUE_VIOLATION:
            # 已有同一目标的 pending 举报（唯一索引兜底，避免并发重复提交）
            return None

    @staticmethod
    def get(report_id: int) -> dict | None:
        """获取单条举报详情。"""
        row = _Report.select().where(_Report.id == report_id).dicts().get_or_none()
        return _report_dict(row) if row else None

    @staticmethod
    def get_all(status: str | None = None) -> list[dict]:
        """获取全部举报（可按状态过滤），按时间倒序。"""
        q = _Report.select().order_by(_Report.id.desc())
        if status:
            q = q.where(_Report.status == status)
        return [_report_dict(row) for row in q.dicts()]

    @staticmethod
    def count(status: str | None = None) -> int:
        """举报总数（可按状态过滤）。"""
        q = _Report.select()
        if status:
            q = q.where(_Report.status == status)
        return q.count()

    @staticmethod
    def handle(report_id: int, handled_by: str, action: str, note: str = "") -> bool:
        """处理举报：action 为 handled（已处理）或 dismissed（驳回/误报）。"""
        if action not in ("handled", "dismissed"):
            return False
        updated = (
            _Report.update(
                status=action,
                handled_by=handled_by,
                handled_at=now_str(),
                note=(note or "")[:500],
            )
            .where((_Report.id == report_id) & (_Report.status == "pending"))
            .execute()
        )
        return updated > 0
