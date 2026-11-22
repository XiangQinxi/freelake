"""
导出与后台工具（freelake/services/admin.py）
===========================================

数据导出（CSV / JSON，敏感列已剔除）与管理员页面的统计、清理工具。
这些函数只被 `pages/admin.py` 调用，改列时同步看那边的表格。
"""

import csv
import io
import os

from peewee import fn

from freelake.config import ATTACHMENTS_DIR, AVATARS_DIR
from freelake.db.adapters import (
    _attachment_dict,
    _comment_dict,
    _post_dicts,
    _report_dict,
    _user_dict,
)
from freelake.db.connection import db
from freelake.db.models import (
    AttachmentModel as _Attachment,
    CommentModel as _Comment,
    PostModel as _Post,
    ReportModel as _Report,
    UserModel as _User,
    now_str,
)

def export_users_csv() -> bytes:
    """导出全部用户为 CSV（utf-8-sig，Excel 可直接打开；不含密码与密钥）。"""
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["userid", "username", "created_at", "description", "role"])
    for u in _User.select().dicts():
        writer.writerow(
            [u["userid"], u["username"], u["created_at"], u["description"], u["role"]]
        )
    return output.getvalue().encode("utf-8-sig")

def export_users_json() -> bytes:
    """导出全部用户为 JSON。

    注意：v2 起剔除 ``password``（哈希）与 ``secret_key``（等价管理员凭据），
    与 CSV 导出口径保持一致；v1 的 JSON 导出会把这两列一并写进下载文件。
    """
    import json

    data = [
        {
            "userid": u["userid"],
            "username": u["username"],
            "created_at": u["created_at"],
            "description": u["description"],
            "avatar": u["avatar"],
            "role": u["role"],
        }
        for u in _User.select().dicts()
    ]
    return json.dumps(data, ensure_ascii=False, indent=2, default=str).encode(
        "utf-8-sig"
    )

def export_posts_csv() -> bytes:
    """导出全部文章为 CSV（含标签、附件数、评论数统计列）。"""
    import csv
    import io

    rows = list(_Post.select().order_by(_Post.id).dicts())
    posts = _post_dicts(rows)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "authorid",
            "title",
            "content",
            "created_at",
            "tags",
            "attachments_count",
            "comments_count",
        ]
    )
    for p, row in zip(posts, rows):
        writer.writerow(
            [
                p["id"],
                p["authorid"],
                p["title"],
                p["content"],
                p["created_at"],
                ";".join(p["tags"]),
                len(p["attachments"]),
                row["comment_count"],
            ]
        )
    return output.getvalue().encode("utf-8-sig")

def export_posts_json() -> bytes:
    """导出全部文章为 JSON（评论导出为 ID 列表；剔除附件密码哈希）。"""
    import json

    posts = _post_dicts(_Post.select().order_by(_Post.id).dicts(), with_comments=True)
    data = []
    for p in posts:
        item = dict(p)
        item["comments"] = [json.loads(c)["id"] for c in p["comments"]]
        item.pop("attpassword", None)
        data.append(item)
    return json.dumps(data, ensure_ascii=False, indent=2, default=str).encode(
        "utf-8-sig"
    )

def export_comments_csv() -> bytes:
    """导出全部评论为 CSV。"""
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "postid", "userid", "content", "created_at"])
    for c in (_comment_dict(row) for row in _Comment.select().order_by(_Comment.id).dicts()):
        writer.writerow(
            [c["id"], c["postid"], c["userid"], c["content"], c["created_at"]]
        )
    return output.getvalue().encode("utf-8-sig")

def export_comments_json() -> bytes:
    """导出全部评论为 JSON。"""
    import json

    data = [
        _comment_dict(row)
        for row in _Comment.select().order_by(_Comment.id).dicts()
    ]
    return json.dumps(data, ensure_ascii=False, indent=2, default=str).encode(
        "utf-8-sig"
    )


# endregion

def get_attachment_stats() -> dict:
    """附件统计（总数 / 总大小），单条聚合查询。"""
    row = (
        _Attachment.select(
            fn.COUNT(_Attachment.id).alias("cnt"),
            fn.COALESCE(fn.SUM(_Attachment.size), 0).alias("total"),
        )
        .dicts()
        .first()
    ) or {}
    return {"count": row.get("cnt") or 0, "total_size": row.get("total") or 0}

def get_all_attachments() -> list[dict]:
    """所有附件 + 所属文章标题（后台附件管理表格用）。"""
    rows = (
        _Attachment.select(
            _Attachment.id,
            _Attachment.post,
            _Attachment.original_name,
            _Attachment.stored_name,
            _Attachment.mime,
            _Attachment.size,
            _Post.title.alias("post_title"),
        )
        .join(_Post, on=(_Attachment.post == _Post.id))
        .order_by(_Attachment.id)
        .dicts()
    )
    result = []
    for row in rows:
        item = _attachment_dict(row)
        item["post_id"] = row["post"]
        item["post_title"] = row["post_title"]
        result.append(item)
    return result

def get_orphaned_attachments() -> list[str]:
    """找出 attachments 目录中未被任何文章引用的孤立文件。"""
    referenced = {
        row["stored_name"]
        for row in _Attachment.select(_Attachment.stored_name).dicts()
    }
    # 目录可能还不存在（全新 clone / 从没上传过附件），不要让它把后台整页带崩
    all_files = set(os.listdir(ATTACHMENTS_DIR)) if os.path.isdir(ATTACHMENTS_DIR) else set()
    return [
        f
        for f in all_files
        if f not in referenced and os.path.isfile(os.path.join(ATTACHMENTS_DIR, f))
    ]

def delete_orphaned_attachments() -> int:
    """删除所有孤立附件文件，返回删除数量。"""
    orphans = get_orphaned_attachments()
    for f in orphans:
        os.remove(os.path.join(ATTACHMENTS_DIR, f))
    return len(orphans)

def get_comment_summary() -> list[dict]:
    """全部评论摘要（含所属文章标题与楼层次序），后台评论管理用。"""
    post_titles = {p.id: p.title for p in _Post.select(_Post.id, _Post.title)}
    rows = (
        _Comment.select()
        .order_by(_Comment.post, _Comment.created_at, _Comment.id)
        .dicts()
    )
    counters: dict[int, int] = {}
    result = []
    for row in rows:
        pid = row["post"]
        index = counters.get(pid, 0)
        counters[pid] = index + 1
        result.append(
            {
                "comment_id": row["id"],
                "post_id": pid,
                "post_title": post_titles.get(pid, "(已删除)"),
                "comment_index": index,
                "userid": row["user_id"],
                "content": row["content"],
                "created_at": row["created_at"],
            }
        )
    return result

def delete_comment_by_id(comment_id: int) -> bool:
    """按 comment_id 删除评论，并同步文章的评论计数。"""
    with db.atomic():
        comment = _Comment.get_or_none(_Comment.id == comment_id)
        if not comment:
            return False
        postid = comment.post_id
        comment.delete_instance()
        _Post.update(comment_count=_Post.comment_count - 1).where(
            _Post.id == postid
        ).execute()
    return True

def search_comments(keyword: str) -> list[dict]:
    """按内容或用户 ID 搜索评论（时间倒序）。"""
    rows = (
        _Comment.select()
        .where(
            (_Comment.content.contains(keyword))
            | (_Comment.user_id.contains(keyword))
        )
        .order_by(_Comment.id.desc())
        .dicts()
    )
    return [_comment_dict(row) for row in rows]

def get_orphaned_comments() -> list[dict]:
    """找出所属文章已不存在的孤立评论。

    v2 用外键 + ``ON DELETE CASCADE``，正常写入不会产生孤儿；函数保留用于检查
    历史遗留数据（外键被临时关闭或旧库导出的数据）。
    """
    rows = (
        _Comment.select()
        .where(_Comment.post_id.not_in(_Post.select(_Post.id)))
        .dicts()
    )
    return [_comment_dict(row) for row in rows]

def delete_orphaned_comments() -> int:
    """删除所有孤立评论，返回删除数量。"""
    rows = (
        _Comment.select(_Comment.id)
        .where(_Comment.post_id.not_in(_Post.select(_Post.id)))
        .dicts()
    )
    ids = [row["id"] for row in rows]
    for cid in ids:
        _Comment.delete().where(_Comment.id == cid).execute()
    return len(ids)
