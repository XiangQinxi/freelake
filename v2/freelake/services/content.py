"""
附件与草稿（freelake/services/content.py）
=========================================

附件落盘 + 元数据入库（含 sha256 指纹 / 是否图片标记）、缩略图与整帖 ZIP 缓存，
以及草稿（含草稿标签）的保存与读取。
"""

import hashlib
import io
import os
import typing
import uuid
import zipfile

import streamlit as st
from PIL import Image

from freelake.config import ATTACHMENTS_DIR, ATTACHMENT_EXT_RE, MAX_ATTACHMENT_BYTES
from freelake.db.adapters import _attachment_dict, _draft_dict, _draft_tag_map
from freelake.db.connection import db
from freelake.db.query import format_size
from freelake.db.models import (
    AttachmentModel as _Attachment,
    DraftModel as _Draft,
    DraftTagModel as _DraftTag,
    TagModel as _Tag,
    now_str,
)

def _read_attachment_file(saved_name: str) -> bytes:
    """读附件字节并按文件名缓存（附件名是 uuid、内容不可变，缓存永远安全）。

    列表页/详情页/打包下载都会重复读同一批文件；不缓存时每次 rerun 都要
    把整篇帖子的附件从磁盘读一遍（多图帖子可达数 MB）。
    """
    file_path = os.path.join(ATTACHMENTS_DIR, saved_name)  # NOQA
    try:
        with open(file_path, "rb") as f:
            return f.read()
    except OSError:
        return b""

def build_attachments_zip(
    post_id: int, entries: tuple[tuple[str, str], ...]
) -> bytes:
    """把一篇帖子的附件打包成 ZIP 字节流（按帖子 + 附件名缓存）。

    entries 是 ((saved_name, original_name), ...) 的元组——用元组而不是
    list，是因为 st.cache_data 的缓存键要求可哈希。附件名是 uuid、内容
    不可变，所以缓存不会过期到错误内容。
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for saved_name, original_name in entries:
            data = _read_attachment_file(saved_name)
            if data:
                zf.writestr(original_name or saved_name, data)
    return buffer.getvalue()

class Attachment:
    @staticmethod
    def save(uploaded_file) -> dict:
        """
        保存上传的文件到本地磁盘，返回文件的元数据。

        参数:
            uploaded_file: streamlit 的上传文件对象 (UploadedFile)

        返回:
            dict: 包含文件元数据的字典

        校验：空文件 / 超过 MAX_ATTACHMENT_BYTES 一律拒绝（抛 ValueError，
        由页面捕获成友好提示）。扩展名只保留字母数字，避免落盘成可执行/可
        渲染的文件名。
        """
        # 读取文件二进制数据
        file_bytes = uploaded_file.getvalue()
        size = len(file_bytes)
        if size == 0:
            raise ValueError("文件为空")
        if size > MAX_ATTACHMENT_BYTES:
            raise ValueError(
                f"文件超过上限 {format_size(MAX_ATTACHMENT_BYTES)}"
            )

        # 生成唯一文件名（保留原始扩展名，但只接受字母数字扩展名）
        ext = os.path.splitext(os.path.basename(uploaded_file.name or ''))[1].lower()
        if not ATTACHMENT_EXT_RE.fullmatch(ext):
            ext = ''
        unique_name = f"{uuid.uuid4().hex}{ext}"

        # 保存文件到 attachments 目录
        file_path = os.path.join(ATTACHMENTS_DIR, unique_name)  # NOQA
        with open(file_path, "wb") as f:
            f.write(file_bytes)

        # 返回元数据（不包含文件内容，只存路径）
        mime = uploaded_file.type or ""
        return {
            "original_name": uploaded_file.name,  # 原始文件名
            "saved_name": unique_name,  # 存储在磁盘的文件名
            "type": mime,  # MIME 类型（如 image/png）
            "size": size,  # 文件大小（字节，按实际字节数而非声明值）
            "sha256": hashlib.sha256(file_bytes).hexdigest(),  # 内容指纹（去重/校验）
            "is_image": mime.startswith("image/"),  # 便于列表页判断要不要出图
            "path": unique_name,  # 相对路径（用于读取时拼接）
        }

    @staticmethod
    @st.cache_data(ttl="10m", max_entries=256)
    def get_thumbnail_bytes(saved_name: str, max_width: int = 300) -> bytes:
        """生成图片附件的缩略图（JPEG），结果按文件名缓存 10 分钟。

        非图片或损坏的文件原样返回；文件缺失返回空字节。
        """
        file_bytes = Attachment.get_file(saved_name)
        if not file_bytes:
            return b""
        try:
            img = Image.open(io.BytesIO(file_bytes))
        except Exception:
            return file_bytes
        if img.mode not in ("RGB", "L", "CMYK"):
            # JPEG 不支持 P（调色板）/ RGBA / LA / I / F / 1 等模式，统一转 RGB
            img = img.convert("RGB")
        if img.width > max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), Image.LANCZOS)  # NOQA
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()

    @staticmethod
    def get_thumbnail_base64(saved_name: str, max_width: int = 300) -> str:
        return base64.b64encode(
            Attachment.get_thumbnail_bytes(saved_name, max_width)
        ).decode()

    @staticmethod
    def get_file(saved_name: str) -> bytes:
        """
        根据保存的文件名读取附件二进制数据。

        参数:
            saved_name: 数据库里存的文件名

        返回:
            bytes: 文件的二进制数据，文件不存在时返回空字节
        """
        # 防目录穿越：与头像读取一致，只取文件名部分
        return _read_attachment_file(os.path.basename(saved_name or ""))

class Draft:
    """草稿：用户可保存未完成的文章，稍后在发布页继续编辑。"""

    @staticmethod
    def save(
        userid: str,
        title: str = "",
        content: str = "",
        tags: typing.List[str] = None,
        attpassword: str = "",
        draft_id: int | None = None,
    ) -> int | None:
        """保存草稿；传入 draft_id 表示更新已有草稿，否则新建。"""
        if not userid:
            return None
        now = now_str()
        if draft_id:
            d = _Draft.get_or_none(
                (_Draft.id == draft_id) & (_Draft.user_id == userid)
            )
            if d is None:
                # 草稿不存在或不属于该用户：返回 None 让调用方报错，
                # 不要静默新建（否则一次越权 ID 就变成一份新草稿）。
                return None
            d.title = title
            d.content = content
            if attpassword is not None:
                d.attpassword = attpassword
            d.updated_at = now
            with db.atomic():
                d.save()
                if tags is not None:
                    _sync_draft_tags(d.id, tags)
            return d.id
        with db.atomic():
            draft = _Draft.create(
                user_id=userid,
                title=title,
                content=content,
                attpassword=attpassword,
                created_at=now,
                updated_at=now,
            )
            _sync_draft_tags(draft.id, tags)
        return draft.id

    @staticmethod
    def get_drafts(userid: str) -> list[dict]:
        """获取某用户的全部草稿（按草稿 ID 倒序）。"""
        rows = (
            _Draft.select()
            .where(_Draft.user_id == userid)
            .order_by(_Draft.id.desc())
            .dicts()
        )
        tag_map = _draft_tag_map(row["id"] for row in rows)
        return [_draft_dict(row, tag_map.get(row["id"])) for row in rows]

    @staticmethod
    def get_draft(userid: str, draft_id: int) -> dict | None:
        """获取单条草稿（仅限本人）。"""
        row = (
            _Draft.select()
            .where((_Draft.id == draft_id) & (_Draft.user_id == userid))
            .dicts()
            .get_or_none()
        )
        if not row:
            return None
        return _draft_dict(row, _draft_tag_map([row["id"]]).get(row["id"]))

    @staticmethod
    def delete(userid: str, draft_id: int) -> bool:
        """删除草稿（仅限本人）。"""
        return (
            _Draft.delete()
            .where((_Draft.id == draft_id) & (_Draft.user_id == userid))
            .execute()
            > 0
        )

    @staticmethod
    def count(userid: str) -> int:
        """某用户的草稿数。"""
        return _Draft.select().where(_Draft.user_id == userid).count()

def _sync_draft_tags(draft_id: int, tags: typing.Iterable[str] | None) -> None:
    """整体替换草稿标签（与文章标签同构，但不维护 usage_count：草稿不算正式发布）。"""
    names: list[str] = []
    for name in tags or []:
        name = (name or "").strip()
        if name and name not in names:
            names.append(name)
    _DraftTag.delete().where(_DraftTag.draft == draft_id).execute()
    for position, name in enumerate(names):
        tag, _ = _Tag.get_or_create(name=name)
        _DraftTag.create(draft=draft_id, tag=tag, position=position)
