"""
FreeLake 测试夹具（tests/conftest.py）
======================================

关键点：api 模块在导入时就会以相对路径 ``data.db`` 连接数据库并建表，
并读取相对路径的 ``config.toml``。为了不污染项目根目录的真实数据，
本 conftest 在导入任何业务模块之前把进程工作目录切换到临时目录
（并放置一份最小 config.toml），使 data.db / config.toml 都落在
临时目录中；进程退出时自动清理。
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# 项目根目录加入 sys.path，保证可以 import api
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# 切换到临时工作目录（必须在导入 api 之前执行）
_TMP_ROOT = tempfile.mkdtemp(prefix="freelake_test_")
with open(os.path.join(_TMP_ROOT, "config.toml"), "w", encoding="utf-8") as f:
    f.write('[admin]\nuserid = "testadmin"\nusername = "Test Admin"\n')
os.chdir(_TMP_ROOT)
atexit.register(lambda: shutil.rmtree(_TMP_ROOT, ignore_errors=True))


@pytest.fixture(autouse=True)
def _clean_db():
    """每个测试结束后清空所有表，避免用例间数据串扰。"""
    import api

    yield
    for model in (
        api._User,
        api._Post,
        api._Comment,
        api._Like,
        api._Bookmark,
        api._Report,
        api._Draft,
    ):
        model.delete().execute()


@pytest.fixture
def tmp_dirs(monkeypatch, tmp_path):
    """把附件/头像目录指向临时目录，避免测试写进仓库。"""
    import api

    monkeypatch.setattr(api, "ATTACHMENTS_DIR", str(tmp_path / "attachments"))
    monkeypatch.setattr(api, "AVATARS_DIR", str(tmp_path / "avatars"))
    os.makedirs(api.ATTACHMENTS_DIR, exist_ok=True)
    os.makedirs(api.AVATARS_DIR, exist_ok=True)
    return api


class FakeUpload:
    """模拟 streamlit 的上传文件对象。"""

    def __init__(self, name: str, data: bytes, mime: str = "application/octet-stream"):
        self.name = name
        self.type = mime
        self.size = len(data)
        self._data = data

    def getvalue(self) -> bytes:
        return self._data
