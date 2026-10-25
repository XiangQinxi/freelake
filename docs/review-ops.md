# FreeLake 工程化与仓库卫生审查

> 审查基线：git HEAD `37f9f485b1878f69c35cd919da0da0bfac93b30d`（"提供分辨率"，2026-08-23 14:27:21 +0800），审查时间 2026-10-25 13:24–13:50（Asia/Shanghai）；仅用只读命令验证（`git status/log/diff/cat-file/count-objects`、`pip show/index versions/check`、`ruff check/format --check --no-cache`、`Get-ChildItem/Get-Content` 等价命令）。未运行 pytest（会 `import api` 建库）、未启动 streamlit、未读取 `.streamlit/secrets.toml`、未修改任何文件。

审查范围：`requirements.txt`、`config.toml`、`.gitignore`、`.streamlit/config.toml`、`static/`、根 `background.mp4`、`v1/`、`bench/`、`docs/`、`AGENT.md`、`.agents/.claude`、git 历史与状态、缺失的工程设施。

---

## P0

### P0-1 `requirements.txt` 声明了一个"依赖幻觉"占位包 `playhouse`，pip 会从第三方拉取
- 证据：`requirements.txt:8` → `playhouse>=0.1`（`v1/requirements.txt:2` 同样有）。`python -m pip show playhouse` 输出：
  `Version: 0.1.0` / `Summary: This package is an example of a Dependency Hallucination. There is no code in this package, it is just a placeholder to prevent exploitation.` / `Home-page: https://303sec.com`；其 `C:\Python\Py313\Lib\site-packages\playhouse-0.1.0.dist-info\METADATA` 第 3 行即上述 Summary，`RECORD` 只有 5 行 dist-info、**没有任何 .py 文件**。
- 影响：`playhouse` 是 peewee 自带的子包（`api.py:38 from playhouse.mysql_ext import JSONField`），本不该也无法作为独立发行版安装。现状是 `pip install -r requirements.txt` 每次构建都从 PyPI 安装一个由陌生方（303sec.com）发布的占位包：今天它没有代码所以"能跑"，但这把构建期信任交给了一个与本项目无关的发布方，未来版本推可执行代码即为供应链入口。同时它掩盖了真实约束——playhouse 的可用性由 peewee 版本决定（`api.py:36-38`）。
- 建议：删除 `requirements.txt:8` 与 `v1/requirements.txt:2`，并在 requirements 顶部加注释"playhouse 由 peewee 自带，禁止单独声明"；CI 里加一步 `pip install -r requirements.lock --dry-run -q | grep -q playhouse && exit 1` 防回潮；顺带对 `peewee>=4.3` 之外的依赖做一次同名幻觉包排查（`streamlit-file-browser`、`streamlit-video-background` 已确认是真实包）。

### P0-2 Cookie 加密密钥有公开硬编码兜底，而 Cookie 里存的是**明文密码**
- 证据：`main.py:55-60` → `password=os.environ.get("COOKIES_PASSWORD") or _secrets_get("cookies", {}).get("password") or "dev-insecure-fallback-change-me"`（`main.py:59`）；`main.py:15` 的模块 docstring 自己写着"``[cookies]`` 段读取（**不要使用默认值**）"，代码与注释矛盾。`pages/login.py:35-37` → `cookies["userid"]=? / cookies["password"] = password / cookies["secretkey"] = ?`（明文）；`main.py:69-70` 再把 `password` 读回会话；`README.md:134` 明确宣称"`userid`/`password` 以加密形式存入浏览器 Cookie"。
- 影响：任何没配 `[cookies].password` 的部署（Streamlit Cloud 后台忘填、键名写错被 `main.py:36-41` 的 `except Exception` 吞掉、本地首次启动）都会用仓库里公开可查的常量当 AES 密钥。此时任何能拿到某用户 Cookie 的人都能解出该用户的**明文密码**，而用户常在多站复用密码。这属于静默 fail-open，不是"降级体验"。
- 建议：改为 fail-closed——`key` 为空时 `st.error("未配置 [cookies].password，拒绝启动")` + `st.stop()`；或退而求其次用 `secrets.token_hex(32)` 每次启动随机生成（代价：重启后全员重新登录，可接受）。启动时用 logger.info 打印密钥来源（`secrets`/`env`/`fallback`）便于排查；本次修复后轮换一次密钥；在 `README.md:57-78` 与 `AGENT.md:143-146` 把该键标为"必填"。

### P0-3 49.7 MiB 的 `background.mp4` 处于"已暂存 + 工作区已删"，`.git` 已被它撑到 56 MiB
- 证据：`git status --porcelain` → `AD background.mp4`；`git cat-file -s :background.mp4` → `52154578`；`git cat-file -s HEAD:background.mp4` → `fatal: path 'background.mp4' does not exist in 'HEAD'`（即只存在于 index/objects，从未进入提交）；`git count-objects -vH` → `count: 555`、`size: 54.24 MiB`、`packs: 0`；`du -sh .git` → `56M`；`git ls-tree -r -l HEAD | awk '{s+=$4} END {...}'` → `HEAD tree total: 4.91 MiB`。
- 影响：仓库 56MB 里 ~50MB 是一个"既不在工作区、也不在任何提交里"的幽灵对象。任何人执行 `git add -A && git commit`（或 IDE 的"提交全部"）都会把 49.7 MiB 写进历史：GitHub 对 >50MB 的单文件告警、>100MB 直接拒收，之后只能 `filter-repo`/BFG 改写历史并让所有协作者重克隆。同时 `git status` 的 `AD` 状态会让后续操作者困惑（`AGENT.md:384` 已把它记为已知问题，但没有量化 .git 体积）。
- 建议：`git restore --staged background.mp4`（只撤暂存，不动工作区）→ 确认 `git status` 干净 → `git gc --prune=now --aggressive`，再 `git count-objects -vH` 复验应回落到 ~4–5 MiB；`.gitignore` 增加 `/*.mp4`（只忽略根目录，保留 `static/` 已跟踪文件）；若确实需要该视频，放 Release 附件或 `static/` 下压缩版。

---

## P1

### P1-1 依赖无锁、无 venv：云端解析到的版本本地从未测过
- 证据：`requirements.txt:6-19` 全部是 `>=` 下限；`python -m pip index versions streamlit` → `LATEST: 1.66.1` / `INSTALLED: 1.61.1`；`AGENT.md:24` 记录本地实测 1.61.1；`python -c "import sys;print(sys.prefix, sys.prefix!=sys.base_prefix)"` → `C:\Python\Py313 False`（全局解释器，无 venv，项目下也无 `.venv/`）；`python -m pip freeze | wc -l` → `790`；`ls requirements*.lock uv.lock poetry.lock pyproject.toml` 均无。
- 影响：Streamlit Cloud 新建环境时 `streamlit>=1.61` 会解析成 1.66.1，而仓库只在 1.61.1 上验证过。Streamlit 小版本破坏性变更已有先例——本项目为 `st.cache` 被移除专门在 `main.py:24-27` 打了兼容补丁。790 个包的全局环境污染进一步放大差异。
- 建议：用 `uv pip compile`（或 pip-tools）生成带 hash 的 `requirements.lock` 并提交，CI 与云端都用锁文件安装；`requirements.txt` 保留为抽象下限清单；本地 `python -m venv .venv`，`.gitignore` 加 `.venv/`；README 的安装步骤改为"锁文件优先"。

### P1-2 直接 import 的 `altair`、以及 `pytest`/`ruff` 均未声明
- 证据：`pages/admin.py:21 import altair as alt`、`:143 st.altair_chart(role_chart)`；`python -m pip show altair` → `Version: 5.5.0`；`requirements.txt` 全文 19 行无 altair。同文件 `:156/232/306/345` 用 `st.dataframe` 隐式依赖 pyarrow（实测 25.0.1，亦未声明）。而 `pytest`、`ruff`（0.15.10）同样不在任何 requirements 中，`AGENT.md:37` 与 `:401` 却要求"改完必须跑 `python -m pytest tests -q`"。
- 影响：今天 altair 靠 streamlit 的传递依赖"碰巧"可用；一旦 streamlit 把图表后端改为可选依赖（pandas/pyarrow 历史上就这样被移出过），`pages/admin.py` 会 ImportError 整页崩。新机器 clone 后按文档第一条命令装不出测试环境。
- 建议：`requirements.txt` 增加 `altair>=5.5`；新建 `requirements-dev.txt`：`pytest>=8`、`ruff>=0.15`、`pytest-cov`；`AGENT.md:34-38` 的启动块补一行 `pip install -r requirements-dev.txt`。

### P1-3 配置读取依赖 CWD、且仓库里有两个 `config.toml`
- 证据：`api.py:524-530` → `open("config.toml", "r+", ...)` / `open("config.toml", "w+", ...)`；`api.py:533-560` 的 `admin_credentials()` 优先级为 `st.secrets > 环境变量 > config.toml`，其中 `:548-549 except Exception: pass` 静默吞掉 secrets 读取异常；被跟踪的同名文件有两份：根 `config.toml`（`[admin]`，174 字节）与 `.streamlit/config.toml`（主题/server，99 行）。
- 影响：`Config.load()` 用相对路径，只在"进程 CWD=仓库根"时成立——`tests/conftest.py:26-29` 与 `bench/_bootstrap.py` 都靠 `os.chdir` 绕开，说明这是已知痛点；从别的目录 `streamlit run` 会直接 `FileNotFoundError`。两个同名 `config.toml` 极易改错；`Config.save()` 会重写被跟踪文件，把本地改动混进 diff。secrets 配错时静默降级到 `config.toml`，表现为"管理员登录不上"却没有任何日志。
- 建议：`Config` 改为 `Path(__file__).resolve().with_name("config.toml")`；`load()` 只读并在缺失时返回 `{}`；删除 `Config.save()`（或改写到 `.streamlit/` 之外的非跟踪路径）；把根 `config.toml` 重命名为 `admin.toml` 或直接并入 secrets，代码只读；启动时记录一行"admin 凭据来源：secrets/env/config.toml"。

### P1-4 847 行重构 + 全部文档/基准仍未提交，无标签、无备份
- 证据：`git diff --numstat api.py` → `847 377`；`git status --porcelain -uall` → `M .gitignore`、`M api.py`、`M tests/conftest.py` + `?? AGENT.md`、`?? docs/model-refactor.md`、`?? docs/model-usage-audit.md`、`?? bench/`（8 个文件）；`git tag` 空；`git branch -a` 只有 `master`/`origin/master`；`git rev-list --count HEAD` = 103；HEAD 日期 2026-08-23，工作区改动到 2026-10-25 仍未提交。
- 影响：整次模型重构、108K 文档（`du -ch docs/*.md AGENT.md` → 108K）、88K bench 只存在于一台机器的磁盘上，无异地备份、无代码评审入口、无法 bisect。`.gitignore` 的 `data.db.legacy-*` 保护也还停留在未提交状态。
- 建议：按逻辑切成 3 个提交并推送：①`bench/`+`docs/`（基线资产，含 `bench/.gitignore`）②`api.py`+`tests/conftest.py` ③`AGENT.md`+`.gitignore`；推送后 `git tag -a v2.0.0 -m "模型 v2 重构"`；后续每次大改先开分支（`refactor/model-v2`）。注意先完成 P0-3，否则 `git add -A` 会把 52MB 视频一起提交。

### P1-5 bench 结果 JSON 写死了本机绝对路径，且不含环境指纹
- 证据：`bench/after-refactor.json:6` 与 `bench/baseline-current.json:6` 都是 `"data_dir": "C:\\Users\\Xiang\\PycharmProjects\\freelake\\bench\\.benchdata"`；`bench/README.md:62` 用一行手写文字记录"peewee 4.3.0 / SQLite 3.49.1"；数据集目录本身已被 `bench/.gitignore:2` 正确忽略（`.benchdata/`）。
- 影响：提交进仓库的是"本机某次跑出来的 ms 数字"，别人无法判断 before/after 差异来自代码还是 CPU/IO；绝对路径还会泄露本机用户名。没有机器可读的环境指纹，回归对比失去可比性。
- 建议：JSON 里 `data_dir` 改为相对路径或只保留 `scale`；新增 `env` 段（`platform.platform()`、`sqlite3.sqlite_version`、`peewee.__version__`、CPU 核数）；结果文件移到 `bench/results/` 并在 README 标注"参考值，非门禁"；若要做回归门禁，用相对 baseline 的比值阈值（如 p95 ≤ 1.5×）而不是绝对值。

### P1-6 无 CI、无 lint/格式化配置，当前 ruff 报 82 条、6 个文件格式不合
- 证据：`ls .github Dockerfile Makefile pyproject.toml .pre-commit-config.yaml ruff.toml mypy.ini pytest.ini .editorconfig` → 全部不存在；`python -m ruff check . --no-cache --exclude v1` → `Found 82 errors`（示例 `tests/test_data_layer.py:479 F841 Local variable 'p2' is assigned to but never used`）；`python -m ruff format --check . --no-cache --exclude v1` → `Would reformat: api.py / bench/bench_queries.py / bench/seed.py / pages/admin.py / pages/home.py / tests/test_data_layer.py`（`6 files would be reformatted, 11 files already formatted`）；`.ruff_cache/` 下同时存在 `0.12.5/` 与 `0.15.10/`，说明用过两个版本且无配置。
- 影响：`AGENT.md:404` 只是"口头要求提交前跑 ruff"，实际结果是 82 条未处理、两个 ruff 版本并存、格式不统一，每次 diff 都混入无意义改动。
- 建议：加 `ruff.toml`（`line-length = 88`、`target-version = "py313"`、`select = ["E","F","W","I"]`，先把 F401/F841 这类真问题修掉，其余用 `per-file-ignores` 收敛）；加 `.github/workflows/ci.yml`：
  - `lint` job：`ubuntu-latest` + `actions/setup-python@v5` python 3.13 + `pip install -r requirements-dev.txt` + `ruff check .` + `ruff format --check .`，预计 ~40s；
  - `test` job：同环境 `python -m pytest tests -q`，预计 ~15s；
  - `on: [push, pull_request]` + `actions/cache` 缓存 pip，整条流水线 < 2 分钟、public repo 零成本。

### P1-7 无虚拟环境，全局 site-packages 已有 12+ 依赖冲突和 13 个损坏残留
- 证据：`python -m pip check` → `gradio 5.38.2 has requirement pandas<3.0,>=1.0, but you have pandas 3.0.5`、`arcade 3.3.2 has requirement pillow~=11.0.0, but you have pillow 12.3.0`、`cx-freeze 8.3.0 has requirement setuptools<=80.4.0,...` 等；每次 pip 调用都打印 `WARNING: Ignoring invalid distribution ~umpy (C:\Python\Py313\Lib\site-packages)`；`ls site-packages/~*` → `~-l, ~andas, ~andas.libs, ~ebsockets, ~egex, ~il, ~pds, ~umpy, ~yarrow, ~~l` 等 13 个目录。
- 影响：本项目要求的 `pandas>=3.0` 与同环境 gradio 直接冲突，`pillow 12.3.0` 与 arcade 冲突——"本地能跑"只是碰巧；`~umpy` 这类半删除目录是 pip 在系统解释器里中断留下的，会让导入随机失败（pip 自己每次都要跳过它）。
- 建议：项目内建 `.venv`（`python -m venv .venv` → `pip install -r requirements.lock`），`.gitignore` 加 `.venv/`；README/AGENT.md 写明激活命令；重建后 `python -m pip check` 必须为空，并可作为一个 CI 步骤。

### P1-8 无日志、无错误上报，异常被静默吞掉
- 证据：全仓库 grep 无 `import logging`；`api.py` 仅 2 处 `print`（`:625` 旧库归档提示、`:897 print(f"{authorid}发布了新文章：{title}")` 调试输出）；`api.py:548-549` 与 `main.py:40-41` 都是 `except Exception: pass`；`grep -c except`：`api.py` 11、`pages/admin.py` 7、`pages/home.py` 2。
- 影响：线上故障只有 Streamlit 页面上的一行 traceback/终端 stderr，没有级别、时间戳、请求上下文；secrets 配错、Cookie 解密失败、附件写盘失败都会表现为"页面空白/登录不上"，无法定位；`pages/admin.py` 的 7 处异常虽有 `st.error(e)`，但同样不落盘、不留历史。
- 建议：`main.py` 顶部 `logging.basicConfig(level=os.environ.get("LOG_LEVEL","INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")`；`api.py` 取 `logger = logging.getLogger("freelake.api")`，把 2 处 print 换成 `logger.info`；把 `except Exception: pass` 至少改成 `except Exception: logger.warning("读取 secrets 失败", exc_info=True)`；`pages/admin.py` 的 `except Exception as e` 分支补 `logger.exception`。Sentry 可选（`sentry-sdk` + `sentry_sdk.init()` 一行），非必须。

### P1-9 生产环境用非持久 SQLite，且没有备份计划
- 证据：`README.md:53-55` 部署到 Streamlit Cloud；`pages/admin.py:489-506` 提供手工 `download_button`（`:497` "下载 data.db 备份"，`:505-506` 文案自述"Streamlit Cloud 上文件系统不持久，请自行保管备份"）；`api.py:616-629` 的 `_archive_legacy_db()` 只在不兼容时把旧库改名成 `.legacy-<时间戳>`（不迁移数据）；仓库内无备份脚本、无 cron、无恢复文档。
- 影响：容器重启/重部署后论坛数据清零；唯一备份手段是"管理员记得点按钮"，且备份落在个人下载目录；出事后没有恢复演练，`data.db.legacy-20261025-130820` 这种现场文件也没人清理/归档。
- 建议：短期——新增 `scripts/backup_db.py`（`shutil.copy2("data.db", f"backups/data-{date}.db")`，保留最近 7 份，`backups/` 加入 `.gitignore`），管理员页显示"最近备份时间"；把 `pages/admin.py:505-506` 的恢复步骤搬进 README 新增的「运维」节。中期——换托管 Postgres（`api.py:46` 的 `SqliteDatabase` 换成 `PostgresqlDatabase`，`db.execute_sql` 的 `PRAGMA`/partial index 需相应改写），或至少把 `data.db` 挂到持久卷。

### P1-10 两个巨型单文件：`api.py` 1924 行、`pages/home.py` 909 行
- 证据：`wc -l api.py` → `1924`；`wc -l pages/home.py` → `909`；`api.py` 的 `# region` 分布：数据库模型 106-340、dict 适配层 343-515、库结构初始化 605-676、数据库接口 679-1577（`Post` 883-1155 占 272 行）、数据导出 1580-1701、管理员工具 1704-1848、启动引导 1848-1924；`pages/home.py`：对话框 107-218、常用方法 221-371、用户信息查询 374-498、文章信息查询 500-705、主页显示 706-909。`api.py` 还有导入即建库的副作用（`:662 init_database()`、`:665-675` 建管理员）。
- 影响：1924 行单文件让评审/冲突/bisect 都很痛苦（本次重构已是 `+847/−377` 的行级大改）；`api.py:662` 使得"只想读一个常量"也必须触碰数据库；`home.py` 一个文件承载三个路由（`?user_id`、`?post_id`、列表），任何改动的回归面都是全站。
- 建议（可直接照搬的分法）：
  - `api.py` → 新包 `freelake/`：`db.py`（46-135：`db`、`BaseModel`、`format_size`/`execute_sql`/`generate_sk_key`、`SCHEMA_VERSION`、`now_str`）；`models.py`（136-347：10 个模型 + `ALL_MODELS`）；`serialize.py`（343-516：`_user_dict`…`_post_dicts`）；`config.py`（518-560：`Config`、`admin_credentials`）；`security.py`（563-602：`sha256`/`hash_password`/`verify_password`/`fix_duplicate_secret_keys`）；`bootstrap.py`（605-676 + 1848-1924）；`repos/user.py`（680-840）、`repos/post.py`（840-1155，含 `_sync_tags`/`_sync_attachments`）、`repos/interaction.py`（1155-1339）、`repos/report_draft.py`（1339-1501）；`services/media.py`（1501-1580）、`services/exports.py`（1580-1701）、`services/maintenance.py`（1701-1848）。
  - **`api.py` 保留为门面**（re-export），因为存在隐式契约：`tests/test_data_layer.py:479-480` 直接用 `api._Post`，`tests/conftest.py:40` 用 `api.ALL_MODELS`，`docs/model-usage-audit.md:431` 记录了这条约定，必须在门面里显式 `from freelake.models import _User, _Post, ALL_MODELS, ...`。
  - `pages/home.py` → `views/list.py`、`views/detail.py`、`views/profile.py` + `components/post_card.py`、`components/comment_list.py` + `dialogs.py`（107-218）+ `helpers.py`（47-108 的 `highlight_keyword`/`highlight_mentions`/`avatar_link`）；`home.py` 只留 87-106 的 `st.query_params` 解析与分派。注意 `st.navigation` 只注册了 `pages/home.py`（`main.py:152`），拆出的模块不要放进 `pages/`，否则会变成新页面。

---

## P2

### P2-1 `.gitignore` 不完备（17 行，且本次补充尚未提交）
- 证据：`.gitignore:1-17`；`git check-ignore -v background.mp4` 无输出（**未忽略**）；`git check-ignore -v .tmp_imports.py` 无输出（rc=1）；审查期间仓库根确实出现过 `?? .tmp_imports.py`（1183 字节，13:25 生成，后被创建方清理），说明临时脚本会落在仓库根且不会被忽略；缺 `.venv/`、`logs/`、`Thumbs.db`、`.DS_Store`；`demo.py/demo2.py/demo3.py` 逐条枚举（`.gitignore:14-16`），而 `AGENT.md:88` 写成 `demo*.py`；本次 `git diff .gitignore` 只加了 `data.db.legacy-*` 一行（尚未提交）。
- 影响：一次 `git add -A` 就会把根目录临时脚本、视频文件带进仓库；`.ruff_cache/` 目前只靠 ruff 自己写的 `.ruff_cache/.gitignore` 才没进 git，换工具/版本即失效。
- 建议：追加
  ```
  .ruff_cache/
  .venv/
  logs/
  *.tmp
  .tmp_*
  Thumbs.db
  .DS_Store
  /*.mp4
  ```
  并用 `demo*.py` 替换第 14-16 行；改完用 `git check-ignore -v .ruff_cache/ .venv/ .tmp_imports.py background.mp4` 自检。

### P2-2 `.agents/`、`.claude/` 被整体忽略，但里面是空 skill 挂载点
- 证据：`.gitignore:1-2`（`.claude`、`.agents`）；`git status --porcelain --ignored=matching` → `!! .agents/`、`!! .claude/`；`find .agents .claude -maxdepth 4` 只有 `.agents/skills/developing-with-streamlit`、`.claude/skills/developing-with-streamlit` 两个空目录；`AGENT.md:89` 也承认是空目录。
- 影响：当前无害（空目录 git 也无法跟踪）。但一旦把团队共享的 skill 内容放进去，会被静默忽略、clone 后消失，属于"配置资产丢失"的隐患。
- 建议：把策略写进 `AGENT.md:87-89`——个人工具保持忽略；团队共享则改成白名单：`.agents/*` + `!.agents/skills/**`，并把可共享的 skill 提交。

### P2-3 无 LICENSE 文件，README 却已声明"All rights reserved"
- 证据：`ls LICENSE` 无文件；`README.md:158-160` → `## 许可证` / `© 2026 XiangQinxi · All rights reserved`；remote 为公开仓库 `https://github.com/XiangQinxi/freelake.git`。
- 影响：公开仓库无许可证即"保留所有权利"，他人不能合法 fork/引用；GitHub 也不会显示 license 徽标。
- 建议：开源就加 `LICENSE`（MIT 或 Apache-2.0）并在 README 顶部加徽标；确实要保留权利则保持现状即可——二选一，并在 `AGENT.md` 记一句避免后来者误加。

### P2-4 未声明 Python 版本
- 证据：`ls runtime.txt .python-version` 均不存在；`AGENT.md:23` 记录 Python 3.13.5；`README.md:53-55` 用 Streamlit Cloud 部署；代码使用 `str | None`/新式 typing（`api.py:31`、`docs/model-refactor.md` 多处）。
- 影响：云端默认 Python 随平台升级漂移，某次平台升级可能换到不支持的 minor 版本或缺少 wheel；本地与云端版本不一致时 bug 难复现。
- 建议：加 `.python-version`（内容 `3.13`），README 部署节注明"Python 3.13"，CI 矩阵固定 `3.13`。

### P2-5 `AGENT.md` 的行号/行数引用已漂移
- 证据：`AGENT.md:141` 写"实现见 `api.py:206 admin_credentials()`"，实际为 `api.py:533`；`AGENT.md:74` 写 `conftest.py (73)`，实际 `wc -l tests/conftest.py` = 66（本次 `git diff tests/conftest.py` 为 −11/+4）；`AGENT.md:88` 写 `demo*.py` 而 `.gitignore:14-16` 是逐条枚举。对照核验正确的部分：`AGENT.md:54` "api.py 1924 行" ✓、`AGENT.md:75` "25 个用例" ✓（`grep -c 'def test_' tests/test_data_layer.py` = 25）。
- 影响：AI/人按行号跳转会落到错误函数上，反而增加误改风险。
- 建议：文档中只引用符号名（`api.py::admin_credentials`）而不是裸行号；或加 `scripts/check_docs.py` 在 CI 里定期校验关键行号。

### P2-6 `.streamlit/config.toml` 含有开发期选项与无消费方的开关
- 证据：`.streamlit/config.toml:2 runOnSave = true`、`:3 enableStaticServing = true`；全仓库只有 `pages/home.py:709` 以 `Path(__file__).resolve().parent.parent / "static" / "bg_embed.mp4"` 方式**直接读文件**，没有任何地方通过 `/app/static/...` URL 访问（grep 无命中）；未配置 `maxUploadSize`（Streamlit 默认 200MB）。
- 影响：`runOnSave` 是本地开发选项，提交后会让线上/他人环境行为不同；`enableStaticServing` 把 `static/` 暴露为公开 URL 却没人使用，属于多余攻击面；`maxUploadSize` 默认 200MB 对附件场景偏大。
- 建议：移除 `runOnSave`（或移到本地 `~/.streamlit/config.toml`）；如无 URL 需求就关掉 `enableStaticServing`，并在 README 说明原因；显式设置 `[server] maxUploadSize`（如 50）与 `headless = true`。

### P2-7 `static/` 两个 mp4 共 4.7MB 入 git，其中 `background_720p.mp4` 已无代码引用
- 证据：`git cat-file -s HEAD:static/background_720p.mp4` → 3483575、`HEAD:static/bg_embed.mp4` → 1225901；`git log --all --name-status -- '*.mp4'` → `9ce9054 A static/background_720p.mp4`、`2f19da7 A static/bg_embed.mp4`、`37f9f48 M static/bg_embed.mp4`（视频改版已在历史里多存一份）；当前代码只引用 `bg_embed.mp4`（`pages/home.py:709`），`background_720p.mp4` 无引用。
- 影响：每改一次视频历史就再长大一份，仓库只增不减；未引用的 3.4MB 是纯死重量。
- 建议：`git rm --cached static/background_720p.mp4` 并从工作区移除（或移到 release 资产）；视频后续走 Git LFS（`.gitattributes`：`*.mp4 filter=lfs`）或 Release 附件 + README 说明下载方式。

### P2-8 `api.py` 有生产 `print`，6 个导出函数高度重复
- 证据：`api.py:897 print(f"{authorid}发布了新文章：{title}")`（发布路径内的调试输出）；`api.py:1581-1703` 六个函数 `export_users_csv/json`、`export_posts_csv/json`、`export_comments_csv/json` 结构几乎一致（`csv.writer` 与 `json.dumps` 各三遍），而 `pages/admin.py:516-557` 已有 6 个 `download_button` 在调用。
- 影响：print 污染 stdout 且无时间/级别；改一个导出字段要同步改 6 处，容易漏。
- 建议：删除该 print（并入 P1-8 的 logger）；抽 `_export(rows, kind, fmt)` 工厂，保留 6 个薄封装以维持 admin 调用点不变。

### P2-9 bench 与 conftest 各自维护一份 "chdir + 最小 config.toml" 引导逻辑
- 证据：`bench/_bootstrap.py`（103 行，`bench/README.md:7` 自述"建临时数据目录、写最小 config.toml、chdir 后导入 api"）与 `tests/conftest.py:22-30`（`tempfile.mkdtemp` + 写 `config.toml` + `os.chdir`）做同一件事，两份实现。
- 影响：若按 P1-3 改配置加载方式，两处都要同步改，漏一处就会让测试/bench 悄悄跑在错误目录并污染真实 `data.db`。
- 建议：抽 `devtools/bootstrap.py`（或 `tests/_env.py`）供两者复用；P1-3 落地后两者应都不再需要 chdir。

### P2-10 缺少统一的命令入口
- 证据：无 `Makefile`/`pyproject.toml`/`justfile`；常用命令散在 `AGENT.md:34-38`、`README.md:44-50`、`bench/README.md:15-24` 三处。
- 影响：新人要拼三份文档才知道怎么装/跑/测；CI 也要重复这三份命令，容易与文档不同步。
- 建议：加最小的 `pyproject.toml`（仅 `[tool.ruff]`、`[tool.pytest.ini_options]`，无需打包），或 12 行 `Makefile`：`install / dev / lint / fmt / test / bench / serve`；文档与 CI 都引用同一入口。

---

## 快速收益（≤30 分钟能做完的 5 件）

1. **`git restore --staged background.mp4` + `git gc --prune=now --aggressive`**（P0-3）：2 分钟，回收约 50MB，把 `.git` 从 56M 降回 ~5M；用 `git count-objects -vH` 复验。
2. **删除 `requirements.txt:8` 的 `playhouse>=0.1`**（P0-1）：1 分钟，阻断第三方占位包的构建期安装。
3. **`main.py:59` 改为 fail-closed**（P0-2）：5 分钟，缺 `[cookies].password` 时 `st.error()` + `st.stop()`，消除"公开常量加密明文密码"。
4. **`.gitignore` 追加 8 行**（P2-1）：2 分钟，覆盖 `.ruff_cache/`、`.venv/`、`logs/`、`*.tmp`、`/*.mp4` 等。
5. **提交现有重构并打标签**（P1-4）：10 分钟，3 个逻辑提交 + `git tag v2.0.0` + push，让 847 行改动与 108K 文档离开单机。

---

## 已排除 / 非问题

- **`docs/model-usage-audit.md:6` 的"api.py(1454 行)"不是漂移**：该文件标题即"重构前基线"，审计时间 2026-10-25 13:02，当时工作区的 `api.py` 为 1454 行的 v1 版本；当前 1924 行是同一份未提交重构之后的结果，两者不矛盾。同理该文件记录的"7 张表"是 v1 结构口径。
- **测试数量声明一致**：`AGENT.md:28/37/75/358`、`docs/model-refactor.md:160/264`、`docs/model-usage-audit.md:429/641` 均写"25 个用例"，`grep -c 'def test_' tests/test_data_layer.py` = 25 ✓。本次只读审查未运行 pytest，**测试是否仍然全绿未验证**（运行会 `import api` 建库，按任务约束跳过）。
- **`.streamlit/secrets.toml` 已被正确忽略**：`git check-ignore -v .streamlit/secrets.toml` → `.gitignore:5:.streamlit/secrets.toml`；文件存在（397 字节），本次未读取内容 ✓。
- **运行时数据均已忽略**：`data.db`（192512 字节，`.gitignore:12`）、`data.db.legacy-20261025-130820`（40960 字节，`:17`）、`attachments/`（5.0K，1 个文件，`:9`）、`avatars/`（60K，`:10`）——`git status --ignored` 均为 `!!`，未跟踪 ✓。
- **`api2.py`（25 行）不是残留**：`AGENT.md:55` 记录在案，`main.py:32` 导入 `check_by_state`，属刻意极薄的会话层，保留正确（虽然文件名带 `2` 不够直观，重命名收益低于风险，未列入问题）。
- **`static/*.mp4` 未被 ignore 属预期**（有意跟踪），问题只在体积与冗余（见 P2-7），不是配置错误。
- **网络可用**：`python -m pip index versions streamlit/playhouse` 均成功返回，依赖校验未因离线跳过；也正因为联网才能发现 `playhouse` 的真实来源。
- **`bench/.gitignore` 与 `bench/README.md` 写得比主 README 还细**（数据集规模、测量方法、before/after 对照、已知回退都齐），这部分工程实践是加分项，仅需按 P1-5 补齐可移植性。
