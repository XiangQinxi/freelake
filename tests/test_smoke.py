"""导入与语法冒烟测试（tests/test_smoke.py）。

拆包与组件抽取最容易引入的回归是「模块被切走、某个名字没跟着搬过去」——
这类错误在 import 阶段就暴露（NameError/ImportError），而数据层测试只覆盖运行期。
所以这里把每个模块都 import 一遍，并把每个页面都 compile 一遍。
"""

import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

MODULES = [
    "api",
    "api2",
    "const",
    "freelake.config",
    "freelake.settings",
    "freelake.logging_setup",
    "freelake.db.connection",
    "freelake.db.models",
    "freelake.db.schema",
    "freelake.db.adapters",
    "freelake.db.query",
    "freelake.services.security",
    "freelake.services.sessions",
    "freelake.services.users",
    "freelake.services.posts",
    "freelake.services.content",
    "freelake.services.interactions",
    "freelake.services.admin",
    "freelake.services.bootstrap",
    "freelake.web.text",
    "freelake.web.dialogs",
    "freelake.web.components",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    assert importlib.import_module(name) is not None


def test_entry_point_compiles():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    compile(source, str(ROOT / "main.py"), "exec")


@pytest.mark.parametrize("path", sorted((ROOT / "pages").glob("*.py")), ids=lambda p: p.name)
def test_page_compiles(path):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
