# FreeLake 项目审查报告

> 审查对象：`C:\Users\Xiang\PycharmProjects\freelake`（Streamlit 校园论坛），只读审查，未修改业务代码。
> 审查基线：git HEAD `37f9f48`（"提供分辨率"，2026-08-23）之上的**未提交工作区**（`api.py` 相对 HEAD 为 +851/−387，模型 v2 重构尚未提交）。
> 审查时间：2026-10-25 13:00–14:10（Asia/Shanghai）。
> 方法：三路并行静态审查（页面层 / 数据层 / 工程化）+ Lead 对高优先级结论逐条复现。
> 分区报告：`docs/review-pages.md`（30 条）、`docs/review-data.md`（29 条）、`docs/review-ops.md`（23 条）；本文件是合并去重后的**总表**，分区报告保留完整证据与代码片段。

严重度定义：

| 级别 | 含义 | 处理时限 |
| --- | --- | --- |
| **P0** | 可导致数据丢失、破坏安全边界、或阻断部署/供应链 | 下次上线前 |
| **P1** | 功能缺陷、显著性能浪费、或工程风险 | 两周内 |
| **P2** | 可读性/一致性/小概率问题 | 顺手修 |

---

## 0. 结论摘要

| 维度 | 数量 | 说明 |
| --- | --- | --- |
| P0 | **4** | 全部经 Lead 复现或命令验证 |
| P1 | **24** | 安全与权限 7、数据正确性 6、性能 6、工程化/可维护 5 |
| P2 | **18** | 合并去重后 |
| 原始条目 | 82 | 三份分区报告合计 |
| 测试状态 | **25 passed in 10.58s** | 审查后 Lead 复跑，重构未破坏既有用例 |

一句话结论：**模型重构本身是成功的（性能与结构都变好），但项目在"权限边界、凭据管理、只读工具、依赖与仓库卫生"四处仍按"内部玩具"的标准在运行**——其中两条能在不越权的情况下清空数据库或解出用户明文密码。建议先修 P0，再按第 5 节路线图推进。

---

## 1. P0（4 条，下次上线前必须修）

### P0-1 后台"只读 SQL"工具可被绕过，能直接删表数据 / 篡改表版本

- 位置：`api.py:79-94`（守卫 85-92）、`pages/admin.py:466-480`（提示语 "写操作已被拦截！"）
- 问题：守卫只看**首个关键字**是否属于 `SELECT/WITH/EXPLAIN/PRAGMA`；`WITH` 与 `PRAGMA` 都不是只读的。

| 手工复现（临时目录中的一次性库，未触碰仓库 `data.db`） | 结果 |
| --- | --- |
| `execute_sql("WITH x AS (SELECT 1) DELETE FROM _post")` | 抛出 `TypeError: 'NoneType' object is not iterable`，**但行已被删除**（`_post` 计数 1 → 0） |
| `execute_sql("PRAGMA user_version = 0")` | 同样先抛 TypeError，**但 `PRAGMA user_version` 已变成 0** |

- 危害链：
  1. `PRAGMA user_version = 0` 后，**下一次进程启动**时 `api.py:653-655` 判定"旧库"→ `_archive_legacy_db()` 把 `data.db` 改名 → 重建 10 张空表，全站数据"消失"（实际在 `.legacy-*` 里，界面无任何提示，只有一行 stdout）。
  2. `WITH ... UPDATE _user SET role='ADMIN'` 一条语句即可提权；`WITH ... DELETE FROM _post` 清空文章。
  3. `PRAGMA foreign_keys = OFF` 会让后续 `on_delete="CASCADE"` 全部失效，删文章留下孤儿评论/点赞/附件记录。
- 修复：换成**编译期**拦截（`conn.set_authorizer` 对 `SQLITE_INSERT/UPDATE/DELETE/CREATE_*/DROP_*/ALTER_TABLE/PRAGMA` 返回 `SQLITE_DENY`），PRAGMA 白名单只保留 `table_info/index_list/foreign_key_list/integrity_check` 等只读项并禁止 `=`；执行前先拒绝空语句。同时把提示语改为与实现一致。

### P0-2 Cookie 加密密钥 fail-open，而 Cookie 里存的是明文密码与永久 secretkey

- 位置：`main.py:55-60`（密钥回落 `"dev-insecure-fallback-change-me"`，`main.py:59`）、`pages/login.py:35-37`、`pages/register.py:55-57`（写入 `password`/`secretkey`）、`main.py:69-70`（读回）、`README.md:134`（对外宣称"以加密形式存入"）
- 问题：没配 `COOKIES_PASSWORD`/secrets 时，用**仓库里公开可见的常量**当加密密码；Cookie 内容却是 `userid` + **明文 password** + `secretkey`。部署方忘配一次（或键名写错、被 `_secrets_get` 的 `except Exception` 吞掉），任何人都能用公开常量解出任意用户明文密码。
- 危害：明文密码在多站复用 → 直接扩散；`secretkey` 是 `check_admin`（`api.py:726-728`）认可的**永久管理员凭据**，无过期、无撤销。
- 修复：fail-closed（缺密钥就 `st.error` + `st.stop`）；不要再写 `password` 进 Cookie，改为随机 session token 存在库中（表加 `session_token_hash`/`expires_at`）；本轮修复后轮换密钥。

### P0-3 49.7 MiB 的 `background.mp4` 卡在"已暂存 + 工作区已删"，一次提交就写进历史

- 证据（命令实测）：

| 命令 | 输出 |
| --- | --- |
| `git status --porcelain -- background.mp4` | `AD background.mp4` |
| `git cat-file -s ":background.mp4"` | `52154578` |
| `git cat-file -s "HEAD:background.mp4"` | `fatal: path 'background.mp4' does not exist in 'HEAD'` |
| `git count-objects -vH` | `count: 555`、`size: 54.24 MiB`、`size-pack: 0 bytes` |
| `.git` 目录大小 | 54.3 MB（HEAD 树实际仅约 4.9 MiB） |

- 危害：约 50MB 的"幽灵对象"只存在于 index/loose objects。任何人执行 `git add -A && git commit` 都会把 49.7MB 写进历史（GitHub 对 >50MB 单文件告警），之后只能 `filter-repo`/BFG 改写并让所有协作者重克隆。
- 修复：`git restore --staged background.mp4` → `git gc --prune=now` → `git count-objects -vH` 复验回落；`.gitignore` 增加 `/*.mp4`（只忽略根目录，`static/` 已跟踪文件不受影响）。

### P0-4 `requirements.txt` 声明了一个"依赖幻觉"占位包，每次构建都从第三方拉取

- 位置：`requirements.txt:8` `playhouse>=0.1`（`v1/requirements.txt:2` 同样）；`api.py:38` 实际用的是 peewee 自带的 `playhouse` 子包。
- 证据（命令实测）：

```
$ pip show playhouse
Version: 0.1.0
Summary: This package is an example of a Dependency Hallucination.
         There is no code in this package, it is just a placeholder to prevent exploitation.
Home-page: https://303sec.com
Files:     playhouse-0.1.0.dist-info\*        ← 只有 dist-info，没有任何 .py
```

- 危害：`playhouse` 是 peewee 的内部子包，不该也无法独立安装。现状是每次 `pip install -r requirements.txt` 都从陌生发布方（303sec.com）安装一个占位包——今天它没有代码所以"能跑"，但这把构建期信任交给了与本项目无关的人。它的存在还掩盖了真实约束（playhouse 的可用性由 peewee 版本决定）。
- 修复：删掉该行，requirements 顶部注明"playhouse 由 peewee 自带，禁止单独声明"；CI 加一步断言环境里没有独立 `playhouse` 发行版。

---

## 2. P1（24 条）

### 2.1 安全与权限（7）

| # | 问题 | 位置 | 影响 | 修复 |
| --- | --- | --- | --- | --- |
| 1 | Cookie 恢复不写入 `secretkey`，管理员刷新后改角色/删用户必失败 | `main.py:67-70` + `pages/admin.py:183`、`:210` | `KeyError: 'secretkey'` 被 `admin.py:189` 吞成"修改失败：'secretkey'"，后台用户管理实际不可用 | 恢复 Cookie 时一并 `state["secretkey"] = cookies.get("secretkey","")`，或让后台改用 `check_admin(password)` 路径 |
| 2 | `User.modify` 的 `role` 无授权边界 | `api.py:798-812` | 任何用户 `modify(自己, 自己密码, role="ADMIN")` 即成管理员（Lead 已复现：`role now: ADMIN`） | 从 `modify` 删掉 `role`，单开 `set_role(admin_secret_key, ...)` 并强制校验 |
| 3 | `avatar` 字段无校验 + `get_avatar_bytes` 对绝对路径直接 `open()` | `api.py:806-807`、`api.py:1563-1564`、渲染点 `pages/home.py:383` | 任意文件读取（Lead 已复现：读临时目录 `secret.txt` 得到 `b'TOPSECRET'`）；配合绝对路径可读 `.streamlit/secrets.toml`、`data.db` | 删除 `os.path.isabs` 分支；`avatar` 必须是 `AVATARS_DIR` 下的 `^[0-9a-f]{32}\.jpe?g$` |
| 4 | `get_secret_key` 无鉴权，且 `secret_key` 是永久免密凭据 | `api.py:774-780`、`api.py:726-728`、`config.toml:3`（admin userid `xqx` 已入库） | 知道 userid 即可取密钥；密钥写进 Cookie 且无过期/撤销；与 P0-2 叠加即管理员接管 | 改为 `_issue_secret_key(userid, password)`；表加过期/撤销；入库存 hash |
| 5 | 后台 SQL 工具把 `_user` 的 `password`/`secret_key` 明文渲染到页面 | `pages/admin.py:475-476`（`st.write(result)`） | 任何 `SELECT * FROM _user` 直接把全站密码哈希与管理员密钥显示在浏览器里，还会进页面缓存/截图 | 结果列做敏感列屏蔽（同 `admin.py:153-155` 的口径），或直接禁止查询 `_user` |
| 6 | 注册无任何输入校验；密码规则与提示语不符 | `api.py:681-720`、`pages/register.py:28-37` | userid/username 可为任意长度与字符（含引号、`<`、控制字符）；提示"必须包含字母和数字"但代码只校验 `any(char.isalpha())`，`abcdefgh` 能注册 | 长度/字符集白名单 + trim；补数字校验；密码规则与文案同步 |
| 7 | 上传无类型/大小限制 | `pages/publish.py:110-125`、`pages/image_generate.py:75`、`.streamlit/config.toml`（未设 `maxUploadSize`） | Streamlit 默认 200MB/文件；附件落盘无上限，可耗尽磁盘 | `[server] maxUploadSize = 50`；`Attachment.save` 校验 MIME 与字节数 |

### 2.2 数据正确性（6）

| # | 问题 | 位置 | 影响 | 修复 |
| --- | --- | --- | --- | --- |
| 8 | 点赞/收藏计数器会漂移，且没有修复入口 | `api.py:1248-1269`、`1285-1330`、`1141-1155`（`Post.recount` 无调用方） | 计数与明细可能永久不一致（首页排序、作者统计都读计数器）；并发下 `OperationalError: database is locked` 未捕获，直接冒泡成 500 | 取消路径用 `delete().where(...).execute()` 判断行数再 `MAX(x-1,0)`；写事务用 `db.atomic("IMMEDIATE")`；后台接一个"重算计数"按钮 |
| 9 | 举报处理无 `status='pending'` 条件 | `api.py:1408-1425` | 已处理/已驳回的举报可被反复改写（`handled_by`/`handled_at` 被覆盖） | `update(...).where((id==) & (status=="pending"))` |
| 10 | `Draft.save` 越权静默新建 + 覆盖 `tags`/`attpassword` | `api.py:1429-1500` | 传别人的 `draft_id` 会新建而不是报错；编辑草稿时未传的字段被默认值覆盖 | 校验 `draft.user_id == user_id`；用 `if tags is not None` 语义 |
| 11 | `Post.edit(tags=None)` 清空全部标签 | `api.py:920-945` | "不改标签"的调用实际清空标签（与"默认不改"的直觉相反） | `None` = 不改，`[]` = 清空，二者区分 |
| 12 | 孤立附件清理与在途上传竞态 | `api.py:1757-1785`、`pages/publish.py:110-125` | 后台点"清理"可能删掉用户刚上传/刚写入但事务未提交的文件 | 先写库后落盘，或清理时排除最近 N 分钟的文件 |
| 13 | 查询参数无边界校验 | `api.py:1013-1045`（`page/page_size`）、`api.py:947-1011`（`keyword` 未转义 `%`/`_`）、`pages/admin.py:310-313`（`int(del_cid)`） | `page<=0`、巨大 `page_size` 直达 SQL；搜索 `%` 返回全表；评论 ID 非数字让整个后台页抛红 | clamp 参数；`LIKE` 用 `\%` 转义；`int()` 套 `try/except` |

### 2.3 性能（6，数字为 Lead 实测或分区报告实测）

| # | 问题 | 位置 | 量化影响 | 修复 |
| --- | --- | --- | --- | --- |
| 14 | 每次 rerun 最多 4 次 PBKDF2-200k 校验 | `api2.py:23-25`、`api.py:591-602`、调用点 `pages/home.py:241/671/720`、`main.py:166` | 单次 `verify_password` 实测 **107–110ms**（3 次采样），首页一次交互 ≈ **0.43s 纯 CPU**；并发下直接占满 worker | 登录时校验一次，session 里存 token 摘要；不要缓存布尔值（v1 已有教训） |
| 15 | 首页/评论区 N+1 查询 | `pages/home.py:241/249/250/671` | 每页 5 篇文章 → 15 次额外查询（登录态/点赞/收藏各 5 次）+ 3 次会话查询 ≈ **20+ 条串行 SQL**；评论 n 条再加 n 次 | 批量接口：`get_configs` 已存在，补 `Like.is_liked_many`/`Bookmark.is_bookmarked_many` |
| 16 | 列表页把每篇附件完整读进内存，却从未使用 | `pages/home.py:895-901` | 每次渲染首页/翻页都读全部附件文件字节，`files` 变量之后无任何引用 | 删掉该 dict（详情页才需要文件内容） |
| 17 | 详情页每次 rerun 重新打 ZIP 并整包下发 | `pages/home.py:567-582` | 无密码附件场景下，任何交互都重建 ZIP（内存 + CPU），大附件时页面卡死 | 改成"点击生成下载包"按钮（`st.fragment`/`on_click`） |
| 18 | 后台每次 rerun 无条件全量取数 | `pages/admin.py:76-78`、`:231`、`:490-503`、`:516-563` | 一次 rerun = 用户表 + 文章表 + 全量评论 + 全量附件 + 整库读取 + 6 份全表导出字节流；`expanded=False` **不阻止**执行 | 图表改聚合 SQL；备份/导出移到按钮后；`st.cache_data` + 显式失效 |
| 19 | 首屏内嵌 1.2MB 视频 + 图片生成结果无上限常驻 | `pages/home.py:709`（`streamlit_video_background` embed → base64，膨胀约 33%）；`pages/image_generate.py:41-44` | 每次 rerun 首页都重新编码 ~1.6MB base64；图片会话可累积数十 MB 且永不清理 | 视频改 URL/静态直链；图片只存最近 N 张并给"清空"按钮 |

### 2.4 工程化与可维护性（5）

| # | 问题 | 证据 | 影响 | 修复 |
| --- | --- | --- | --- | --- |
| 20 | 无日志设施，关键异常被静默吞掉 | 全仓库无 `import logging`；`api.py:548`、`:836`、`:1200`、`:1538`、`main.py:40` 均 `except Exception: pass/return`；`api.py:897` 生产 `print` | 线上表现为"页面空白/登录不上/管理员改不了用户"，无时间戳无级别无上下文 | `logging.basicConfig` + 各模块 logger；`pass` 至少换成 `logger.warning(..., exc_info=True)` |
| 21 | 无 venv、无锁文件、直接依赖未声明 | 全局解释器（`sys.prefix == sys.base_prefix`）；`requirements.txt` 全为 `>=`；`pages/admin.py:21 import altair`（实测 5.5.0）、`st.dataframe` 需 pyarrow（25.0.1）、`pytest`/`ruff` 均未声明 | 云端会解析到与本地不同的版本（`pip index versions streamlit` LATEST 已高于本地）；新机器按文档装不出测试环境 | `uv pip compile` 生成 `requirements.lock`；`requirements-dev.txt` 声明 pytest/rufff/pytest-cov；README 与 AGENT.md 同步 |
| 22 | 无 CI、无 lint/格式化配置 | 无 `.github/`、`pyproject.toml`、`ruff.toml`；`ruff check` 报 82 errors、`ruff format --check` 报 6 个文件待格式（由 review-ops 实测） | "改完必须跑 ruff/pytest"只是口头约定，实际 82 条未处理 | 加 `ruff.toml` + `.github/workflows/ci.yml`（lint + pytest 两个 job，<2 分钟） |
| 23 | 两个巨型单文件 | `api.py` 1924 行、`pages/home.py` 909 行（一个文件承载 `?user_id`/`?post_id`/列表三个路由） | 评审/冲突/bisect 成本高；任何改动回归面是全站 | `api.py` → `freelake/{db,models,serialize,config,security,bootstrap,repos/*,services/*}.py`，**保留 `api.py` 作门面 re-export**（`tests/conftest.py:40` 用 `api.ALL_MODELS`、`tests/test_data_layer.py` 用 `api._Post`）；`home.py` → `views/{list,detail,profile}.py` + `components/*`，`home.py` 只留 query_params 分派（拆出的模块不要放 `pages/`，否则会变成新页面） |
| 24 | 导入期副作用 + 路径依赖 CWD | `api.py:46`（`SqliteDatabase("data.db")` 相对 CWD）、`:55-57`（附件目录按 `__file__`）、`:662` 建库、`:665-675` 建管理员、`:1921` 发公告；`api.py:525` `open("config.toml","r+")` | 换个 CWD 启动就得到空库（附件还在项目里）→ "帖子全没了"；只读部署/文件缺失时 `FileNotFoundError` 直接崩；任何脚本一 `import api` 就写盘 | DB 路径锚到 `__file__`/`FREELAKE_DB`；初始化收进显式 `init()` 由 `main.py` 调用；`Config.load()` 只读、缺失返回 `{}` |

---

## 3. P2（18 条，摘要）

| # | 问题 | 位置 |
| --- | --- | --- |
| 1 | 时间字段用本地时间字符串、无时区，服务器换时区会让排序/日期边界错乱 | `api.py:131-133`（`now_str`） |
| 2 | `register` 的 `exists()` → `create()` 之间无竞态保护 | `api.py:681-720` |
| 3 | `Post.publish` 不校验 `authorid` 是否存在（测试自己就用不存在的作者） | `api.py:888-940` |
| 4 | `Report.add` 允许 `postid`/`commentid` 同时为空或指向不存在目标 | `api.py:1339-1394` |
| 5 | `_Tag` 无白名单/无回收，任意标签永久驻留 | `api.py:192-216` |
| 6 | 评论按"文章内下标"定位，索引漂移会删错评论 | `api.py:869-882`、`:1100-1140` |
| 7 | 导出函数在数据层无鉴权（5 个 `export_*` 直接可调） | `api.py:1581-1701` |
| 8 | 死代码：`_user_dict`、`get_configs`、`get_thumbnail_base64` 无调用方（`Post.recount` 见 P1-8） | `api.py:348`、`:757`、`:1214` |
| 9 | `st.rerun()` 全仓库 38 处，交互后整页重跑 | `main.py`/`pages/*.py` |
| 10 | 首页默认日期起点硬编码 `2026-08-01`，更早文章默认不可见 | `pages/home.py:41` |
| 11 | `.streamlit/config.toml` 含开发期开关与无消费方配置（`runOnSave`、`enableStaticServing`），未设 `maxUploadSize`/`headless` | `.streamlit/config.toml:2-3` |
| 12 | `.gitignore` 缺口（`.venv/`、`logs/`、`.tmp_*`、`/*.mp4`、`.ruff_cache/`） | `.gitignore:1-17` |
| 13 | 无 `LICENSE`（README 写 All rights reserved）、无 `.python-version` | 仓库根 |
| 14 | `AGENT.md` 行号漂移（`api.py:206`→实际 533、`conftest.py (73)`→实际 66、`demo*.py`→实际逐条枚举） | `AGENT.md:74/88/141`（本次已修正 3 处） |
| 15 | 8 个导出函数结构几乎一致（csv/json × 3 类），改字段要改 6 处 | `api.py:1581-1701` |
| 16 | `pages/home.py:76-82` 用 f-string 拼 HTML 且 `userid` 未转义；因 Streamlit `st.html` 走 DOMPurify 净化，**降级为属性注入/排版破坏，不构成 XSS** | `pages/home.py:76-82` |
| 17 | `page_icon="logo.ico"` 指向不存在的文件；分页大小 5 在两处硬编码；`st.switch_page` 与 `page_link` 用法自相矛盾 | `main.py:44`、`pages/home.py:826`、`pages/publish.py:132` |
| 18 | `config.toml` 把所有语义色改成灰阶，错误/成功/警告失去颜色区分（可读性/A11y） | `config.toml:31-37`、`.streamlit/config.toml` 主题段 |

---

## 4. 组合风险链（单条看是 P1，串起来是 P0）

```
[未配 cookies 密钥]                          ← P0-2
   └─ Cookie 用公开常量加密明文 password + secretkey
        ├─ 离线解出任意用户明文密码（含管理员）
        └─ 拿到 secretkey 后免密通过 check_admin      ← P1-4
[管理员误用 SQL 工具]
   ├─ WITH ... DELETE/UPDATE 直接改库              ← P0-1
   └─ PRAGMA user_version=0 → 下次启动归档并重建空表 ← P0-1
[数据层无权限边界]
   ├─ modify(role="ADMIN") 自提权                  ← P1-2
   └─ modify(avatar="C:/..../.streamlit/secrets.toml")
        └─ get_avatar_bytes 绝对路径直读            ← P1-3
```

结论：**修 P0-1/P0-2 时不要只改 UI 层**——`User.modify`、`get_secret_key`、`get_avatar_bytes`、`execute_sql` 这四个数据层入口才是唯一权威边界，权限判断必须下沉到它们里面。

---

## 5. 修复路线图

### 第 0 天（≤1 小时，共 5 件速赢）
1. `git restore --staged background.mp4` + `git gc --prune=now`（回收约 50MB，P0-3）。
2. 删 `requirements.txt:8` 的 `playhouse>=0.1`（P0-4）。
3. `main.py:59` 改为 fail-closed，同时从 Cookie 移除 `password`（P0-2 第一步）。
4. `main.py:67-70` 恢复 `secretkey`（P1-1，1 行）。
5. `.gitignore` 补 5 行（`.venv/`、`logs/`、`.tmp_*`、`.ruff_cache/`、`/*.mp4`）。

### 第 1 周（安全与数据边界）
- `execute_sql` 换 authorizer + PRAGMA 只读白名单（P0-1）。
- `modify` 拆出 `set_role`；`avatar` 白名单校验；删掉 `get_avatar_bytes` 的绝对路径分支（P1-2/3）。
- `get_secret_key` 改为需密码签发；`secret_key` 入库存 hash + 过期（P1-4）。
- 后台 SQL 结果屏蔽 `password`/`secret_key`（P1-5）。
- 注册校验 + 密码规则对齐（P1-6）；`maxUploadSize` 与附件大小上限（P1-7）。
- 举报 pending 条件、草稿归属、`tags=None` 语义、计数修复入口（P1-8/9/10/11）。

### 第 2 周（性能与结构）
- 鉴权缓存（4 次 PBKDF2 → 1 次，省 ~0.43s/交互，P1-14）。
- 批量查询接口（P1-15）、删死 I/O（P1-16）、ZIP 按需（P1-17）、后台取数移到按钮后 + 聚合下推（P1-18）。
- 日志设施（P1-20）、`requirements.lock` + `requirements-dev.txt`（P1-21）、CI + ruff（P1-22）。
- `api.py`/`home.py` 按第 2.4 节边界拆分，`api.py` 保留门面（P1-23）。
- 路径锚定 `__file__` + 显式 `init()`（P1-24）。

### 持续
- 每个新特性先补测试（当前 25 个用例覆盖数据层主路径，UI/图片生成/Cookie 生命周期无覆盖）。
- `ruff format` 一次性收敛 6 个文件，之后 CI 强制。

---

## 5.1 实施状态（2026-10-25 更新）

> 用户「开始」后按本路线图执行。以下逐项标注实际结果；未完成项在最后一节。

### 已完成

| 项 | 结果 | 证据 |
| --- | --- | --- |
| 第 0 天 5 件速赢 | 全部完成 | `.git` 54.3MB → 5.1MB（`git count-objects -vH`：count 0 / size-pack 5.02 MiB）；`requirements.txt` 删 `playhouse` 并补 `altair`；Cookie 密钥改进程随机 + 告警；`secretkey` 随 Cookie 恢复；`.gitignore` 分组重写 |
| P0-1 只读 SQL 可绕 | 修 | 首词白名单 + `sqlite3.set_authorizer` 双层；`WITH … DELETE`、`UPDATE`、`INSERT`、`PRAGMA user_version=0`、`PRAGMA journal_mode=WAL` 全部被拒且库无变化 |
| P0-2 Cookie 密钥 fail-open | 修 | `COOKIES_PASSWORD` → `secrets[cookies]` → 进程级 `token_urlsafe(32)` + `st.warning`（fail-safe，不硬停） |
| P0-3 幽灵 blob | 修 | `git restore --staged background.mp4` + `git gc --prune=now` |
| P0-4 playhouse 幻觉包 | 修 | 删行 + 注释说明（peewee 自带子包） |
| P1 secretkey 永久凭据 | 修 | `SCHEMA_VERSION=3`：只存 `secret_key_hash` + `secret_key_expires_at`（30 天），`issue_secret_key(userid, password)` 签发即轮换 |
| P1 自提权 `modify(role=)` | 修 | `modify` 删除 `role` 形参；新增 `set_role(...)`（需 secret_key 或会话内管理员本人） |
| P1 任意文件读头像 | 修 | `get_avatar_bytes` 删除绝对路径分支；`_AVATAR_FILE_RE` 白名单 + `is_valid_avatar_name` |
| P1 后台 SQL 结果泄露哈希 | 修 | `mask_sensitive_rows()`（password / secret_key* / attpassword → `***`） |
| P1 注册无校验 / 密码规则不一致 | 修 | `is_valid_password`（≥8 含字母数字）+ userid 正则 + 注册页数字校验与报错 |
| P1 上传无上限 | 修 | `MAX_ATTACHMENT_BYTES = 20MB` + 扩展名白名单 + `maxUploadSize = 20` |
| P1 后台无页面级鉴权 | 修 | `pages/admin.py`、`pages/image_generate.py` 顶部 `User().is_admin(...)` 门；匿名直连 URL 实测被拒、数据不再泄露 |
| P1 4×PBKDF2/rerun | 修 | `verify_password` 成功结果会话内缓存 5 分钟（键含存储哈希）；AppTest 实测 PBKDF2 调用 4 → 0/rerun，页面 rerun 186ms |
| P1 列表页死 I/O | 修 | 删掉 `pages/home.py` 里「整帖附件读盘后从未使用」的 `files` 字典 |
| P1 附件重复读盘 / ZIP 每次重打 | 修 | `_read_attachment_file`（30 分钟缓存）+ `build_attachments_zip(post_id, entries)`（30 分钟缓存） |
| P1 后台每次 rerun 三表全量 | 修 | `_admin_snapshot(version)`（2 分钟缓存）+ `_refresh()` 版本自增 |
| 测试 | 25 → 32 用例，全绿 | `python -m pytest tests -q` → 32 passed（新增 7 个：SQL 绕过、脱敏、注册校验、提权、密钥签发/过期、头像白名单、附件大小） |

### 未完成（留给下一轮）

1. **Cookie 里仍存明文 `password`**（P0-2 第二步）：改成会话 token 需要新表/新列与页面改造，单独一轮做。
2. **`api.py` 1924 行拆分**：按 §2.4 边界拆 `auth/models/adapters/queries/exports/admin_tools`，`api.py` 保留门面（测试用 `api.ALL_MODELS`/`api._post`/`api._Post`，页面 `from api import ...`）。
3. **`pages/home.py` 909 行三路由拆分**。
4. **举报 pending 条件、草稿归属、`tags=None` 语义**（P1-8/9/10/11）：低风险但本轮未动，需逐一改测试。
5. **日志设施 / requirements.lock / CI / ruff**：`ruff check` 当前 116 errors（其中 108 是 `from peewee import *` 的 F405，改成显式 import 才能收敛）。
6. **路径锚定 `__file__`**（数据库/附件相对 CWD 的问题）。

## 6. 已排除 / 对分区报告的修正

1. **"取消点赞无条件 -1"（review-data P0-3）不成立**：`api.py:1256-1261` 先 `like.delete_instance()` 再减一，只有真的找到并删掉才减。真实风险是并发（`database is locked` 未捕获）与"没有修复入口"，已在 P1-8 按实际情况记录。
2. **`modify(role=...)`/`get_avatar_bytes` 的绝对路径读文件是"数据层可达、UI 不可达"**：当前没有任何页面把 `role` 或绝对路径 `avatar` 传进 `modify`，所以降为 P1（潜在 P0）；一旦新增"编辑资料"页或复用 `modify`，立即成为 P0。
3. **`pages/home.py:76` 不构成 XSS**：Streamlit 的 `st.html` 用 DOMPurify 净化（`streamlit/elements/html.py:56-64`、`static/js/purify.es.*.js`），事件处理器与 `javascript:` 会被剥离；仍建议转义 `userid`（属性注入/排版破坏）。
4. **`st.cache_data` 缩略图缓存没问题**：`api.py:1188-1189` 已带 `ttl="10m", max_entries=256`。
5. **`export_*` 已脱敏**：重构后 JSON 导出已剔除 `password`/`secret_key`/`attpassword`（v1 的裸 `.dicts()` 已改）。
6. **分区报告里的"api.py 1454 行"不是漂移**：那是重构前基线口径；当前 1924 行。
7. **测试仍然全绿**：`python -m pytest tests -q` → 25 passed in 10.58s（本次审查后复跑）。

---

## 7. 复现命令

```bash
# 依赖幻觉包（P0-4）
python -m pip show playhouse

# 幽灵 blob 与仓库体积（P0-3）
git status --porcelain -- background.mp4
git cat-file -s ":background.mp4"
git cat-file -s "HEAD:background.mp4"
git count-objects -vH

# 只读 SQL 守卫与 PRAGMA（P0-1）：在临时目录执行，避免碰真实 data.db
#   execute_sql("WITH x AS (SELECT 1) DELETE FROM _post")   -> 行被删除
#   execute_sql("PRAGMA user_version = 0")                  -> user_version 变成 0

# PBKDF2 成本（P1-14）
python -c "import time,api; h=api.hash_password('x'); t=time.perf_counter(); api.verify_password('x',h); print((time.perf_counter()-t)*1000,'ms')"

# 测试
python -m pytest tests -q
```

---

## 8. 分区报告索引

| 文件 | 条目 | 覆盖面 | 基线 |
| --- | --- | --- | --- |
| `docs/review-pages.md` | 30（P0×3 / P1×18 / P2×9） | `main.py`、`pages/*.py`、`api2.py`、`.streamlit/config.toml` | HEAD `37f9f48`，纯静态 |
| `docs/review-data.md` | 29（P0×6 / P1×11 / P2×12）+ 13 项测试覆盖缺口 | `api.py`、`const.py`、`tests/*.py` | HEAD `37f9f48` 之上的未提交工作区 |
| `docs/review-ops.md` | 23（P0×3 / P1×10 / P2×10） | requirements、`.gitignore`、`static/`、`bench/`、`docs/`、git 历史与状态 | HEAD `37f9f48`，仅只读命令 |

各分区报告的编号（如 `review-data` 的 P0-1）与本文件的 P0-1 编号**互不对应**，引用时请写全"分区名 + 编号"。
