"""
行 → 对外 dict 适配层（freelake/db/adapters.py）
===============================================

v4 的列名是 snake_case（author_id / user_id / post_id ...），而页面与测试
仍按 v1 的键名取值（authorid / userid / postid）。这一层负责两者之间的映射：
**改模型可以，改这里之前先看 pages/ 怎么取值**。
"""

import json

from peewee import fn

from freelake.db.models import (
    USER_ACTIVE,
    AttachmentModel as _Attachment,
    CommentModel as _Comment,
    DraftTagModel as _DraftTag,
    PostModel as _Post,
    PostTagModel as _PostTag,
    TagModel as _Tag,
)

# region 行 → 对外 dict（v1 键名适配层）
# 页面脚本依赖的是「键名契约」而不是物理列名，因此所有对外函数统一走这里转换，
# 重构表结构时页面零改动。键名请勿随意修改（home.py / admin.py / tests 都按此读）。


def _user_dict(row) -> dict:
    """_user 行 → v1 形状 dict（键名与旧库一致）。"""
    return {
        "userid": row["userid"],
        "username": row["username"],
        "password": row["password_hash"],
        "created_at": row["created_at"],
        "description": row["description"],
        "avatar": row["avatar"],
        "role": row["role"],
        # v4 新增的账号运维字段（管理后台表格用；不含任何密钥）
        "status": row.get("status", USER_ACTIVE),
        "last_login_at": row.get("last_login_at", ""),
        "updated_at": row.get("updated_at", ""),
    }


def _comment_dict(row) -> dict:
    """_comment 行 → v1 形状 dict。"""
    return {
        "id": row["id"],
        "postid": row["post"],
        "userid": row["user_id"],
        "content": row["content"],
        "created_at": row["created_at"],
    }


def _report_dict(row) -> dict:
    """_report 行 → v1 形状 dict（admin.py 的举报卡片按这些键渲染）。"""
    return {
        "id": row["id"],
        "postid": row["post_id"],
        # v4 用 target_type/target_id 统一表达举报对象，这里还原 v1 契约：
        # commentid 为 None 表示「举报的是文章」。
        "commentid": row["target_id"] if row["target_type"] == "comment" else None,
        "target_userid": row["target_user_id"],
        "reporter_userid": row["reporter_id"],
        "content_preview": row["content_preview"],
        "reason": row["reason"],
        "status": row["status"],
        "handled_by": row["handled_by"],
        "handled_at": row["handled_at"],
        "note": row["note"],
        "created_at": row["created_at"],
    }


def _draft_dict(row, tags: list[str] | None = None) -> dict:
    """_draft 行 → v1 形状 dict（标签来自 _draft_tag 关联表）。"""
    return {
        "id": row["id"],
        "userid": row["user_id"],
        "title": row["title"],
        "content": row["content"],
        "tags": tags or [],
        "attpassword": row["attpassword"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _attachment_dict(row) -> dict:
    """_attachment 行 → v1 附件 dict（home.py 读 saved_name/original_name/type/size）。"""
    return {
        "saved_name": row["stored_name"],
        "original_name": row["original_name"],
        "type": row["mime"],
        "size": row["size"],
    }


def _draft_tag_map(draft_ids) -> dict[int, list[str]]:
    """批量取草稿标签：{draft_id: [标签名, ...]}（一次查询，按保存顺序）。"""
    if not draft_ids:
        return {}
    rows = (
        _DraftTag.select(_DraftTag.draft, _Tag.name)
        .join(_Tag, on=(_DraftTag.tag == _Tag.id))
        .where(_DraftTag.draft.in_(list(draft_ids)))
        .order_by(_DraftTag.draft, _DraftTag.position)
        .dicts()
    )
    result: dict[int, list[str]] = {}
    for row in rows:
        result.setdefault(row["draft"], []).append(row["name"])
    return result


def _tag_map(post_ids) -> dict[int, list[str]]:
    """批量取文章标签：{post_id: [标签名, ...]}（一次查询，按发布时选择顺序）。"""
    if not post_ids:
        return {}
    rows = (
        _PostTag.select(_PostTag.post, _Tag.name)
        .join(_Tag, on=(_PostTag.tag == _Tag.id))
        .where(_PostTag.post.in_(list(post_ids)))
        .order_by(_PostTag.post, _PostTag.position)
        .dicts()
    )
    result: dict[int, list[str]] = {}
    for row in rows:
        result.setdefault(row["post"], []).append(row["name"])
    return result


def _attachment_map(post_ids) -> dict[int, list[dict]]:
    """批量取文章附件：{post_id: [附件 dict, ...]}（一次查询）。"""
    if not post_ids:
        return {}
    rows = (
        _Attachment.select()
        .where(_Attachment.post.in_(list(post_ids)))
        .order_by(_Attachment.id)
        .dicts()
    )
    result: dict[int, list[dict]] = {}
    for row in rows:
        result.setdefault(row["post"], []).append(_attachment_dict(row))
    return result


def _comments_map(post_ids) -> dict[int, list[str]]:
    """批量取文章的评论（按楼层次序），值为 json 字符串列表（v1 契约）。"""
    if not post_ids:
        return {}
    rows = (
        _Comment.select()
        .where(_Comment.post.in_(list(post_ids)))
        .order_by(_Comment.post, _Comment.created_at, _Comment.id)
        .dicts()
    )
    result: dict[int, list[str]] = {}
    for row in rows:
        result.setdefault(row["post"], []).append(
            json.dumps(_comment_dict(row), ensure_ascii=False)
        )
    return result


def _last_comment_map(post_ids) -> dict[int, dict]:
    """批量取每篇文章的最后一条评论：{post_id: 评论 dict}（首页卡片用）。"""
    if not post_ids:
        return {}
    sub = (
        _Comment.select(fn.MAX(_Comment.id).alias("cid"))
        .where(_Comment.post.in_(list(post_ids)))
        .group_by(_Comment.post)
    )
    rows = _Comment.select().where(_Comment.id.in_(sub)).dicts()
    return {row["post"]: _comment_dict(row) for row in rows}


def _post_dicts(rows, *, with_comments=False, with_last_comment=False) -> list[dict]:
    """把 _post 行列表转成 v1 形状的 dict 列表（批量补齐标签/附件/评论）。"""
    rows = list(rows)
    post_ids = [row["id"] for row in rows]
    tag_map = _tag_map(post_ids)
    attachment_map = _attachment_map(post_ids)
    comments_map = _comments_map(post_ids) if with_comments else {}
    last_map = _last_comment_map(post_ids) if with_last_comment else {}

    result = []
    for row in rows:
        pid = row["id"]
        result.append(
            {
                "id": pid,
                "authorid": row["author_id"],
                "author_name": row.get("author_name", ""),
                "title": row["title"],
                "content": row["content"],
                "created_at": row["created_at"],
                "attachments": attachment_map.get(pid, []),
                "attpassword": row["attpassword"],
                "tags": tag_map.get(pid, []),
                "comments": comments_map.get(pid, []),
                "views": row["views"],
                # 以下三项是 v2 新增的只读统计（v1 没有，页面可按需使用）
                "like_count": row["like_count"],
                "bookmark_count": row["bookmark_count"],
                "comment_count": row["comment_count"],
            }
        )
    if with_last_comment:
        for item in result:
            item["last_comment"] = last_map.get(item["id"])
    return result


# endregion