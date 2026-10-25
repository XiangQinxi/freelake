"""FreeLake 性能基准 · 引导工具（bench/_bootstrap.py）
====================================================

`api.py` 在 **import 时** 就会连接相对路径的 ``data.db``、建表、引导管理员、
修复 secret_key 并尝试发布公告；``data.db`` 与 ``config.toml`` 都相对当前工作
目录。因此任何基准脚本都必须：

1. 先把进程工作目录切到受控的数据目录；
2. 在该目录写一份最小 ``config.toml``（只含 ``[admin]`` 的 userid/username，
   **不写 password**，这样 import 时不会创建管理员、也不会自动发布公告，
   数据规模完全可控）；
3. 之后才 ``import api``。

写法参照 ``tests/conftest.py:26-30``（那里是 chdir 到临时目录再 import api）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 仓库根目录（bench/ 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 最小配置：无 password → api.py 启动引导不创建管理员、不发公告
MIN_CONFIG_TOML = '[admin]\nuserid = "benchadmin"\nusername = "Bench Admin"\n'

# 数据集自描述文件（由 seed.py 写入、由 bench_queries.py 读取）
MANIFEST_NAME = "bench_manifest.json"


def ensure_project_on_path() -> None:
    """把仓库根目录加入 sys.path，保证 `import api` / `import const` 可用。"""
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def resolve_data_dir(data_dir: str | Path | None) -> Path:
    """解析数据目录：显式传入优先，否则读 bench/.last_data_dir.json。"""
    if data_dir:
        return Path(data_dir).expanduser().resolve()
    pointer = PROJECT_ROOT / "bench" / ".last_data_dir.json"
    if pointer.exists():
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        return Path(payload["data_dir"]).expanduser().resolve()
    raise SystemExit(
        "未找到数据集：请先运行 `python bench/seed.py --scale 1`，"
        "或用 --data-dir 指定 seed.py 生成数据的目录。"
    )


def prepare_env(data_dir: str | Path, *, reset: bool = False):
    """chdir 到数据目录并导入 api，返回已初始化的 api 模块。

    Args:
        data_dir: 数据集目录（data.db / config.toml 都落在这里）。
        reset: 为 True 时先删除已有的 data.db，保证干净重建（seed 用）。
    """
    target = Path(data_dir).expanduser().resolve()
    if target == PROJECT_ROOT:
        raise SystemExit("拒绝在项目根目录运行基准脚本（会污染真实 data.db）")

    target.mkdir(parents=True, exist_ok=True)
    db_file = target / "data.db"
    if reset and db_file.exists():
        db_file.unlink()

    config_file = target / "config.toml"
    if not config_file.exists():
        config_file.write_text(MIN_CONFIG_TOML, encoding="utf-8")

    # 必须在 import api 之前切换工作目录
    os.chdir(target)
    ensure_project_on_path()

    import api  # noqa: PLC0415  —— 故意延迟导入：必须 chdir 之后再导入

    return api


def write_manifest(data_dir: str | Path, payload: dict) -> Path:
    path = Path(data_dir).resolve() / MANIFEST_NAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_manifest(data_dir: str | Path) -> dict:
    path = Path(data_dir).resolve() / MANIFEST_NAME
    if not path.exists():
        raise SystemExit(f"缺少数据集描述文件 {path}，请重新运行 bench/seed.py")
    return json.loads(path.read_text(encoding="utf-8"))


def configure_stdout() -> None:
    """Windows 控制台默认 GBK，强制 UTF-8 输出避免中文打印报错。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
