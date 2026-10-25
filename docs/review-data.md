# FreeLake 数据层与测试审查（api.py / const.py / tests）

> 审查基线：git HEAD `37f9f485b1878f69c35cd919da0da0bfac93b30d`（2026-08-23 14:27:21 +0800）**之上的未提交工作区**——`git diff --stat` 显示 `api.py` +851/−387、`tests/conftest.py`、`.gitignore` 有改动，`AGENT.md`/`bench/`/`docs/` 未跟踪；本报告审查的是工作区当前内容，非 HEAD 提交内容。
>
> 方法：对 `api.py`(1924 行)、`const.py`(35)、`api2.py`(25)、`tests/conftest.py`(66)、`tests/test_data_layer.py`(579) 做纯静态阅读；未 `import api`、未启动 streamlit、未读 `.streamlit/secrets.toml`、未改 `data.db` 与 `data.db.legacy-*`。仅在 `/tmp` 用独立 sqlite3/hashlib 脚本做了 3 条机制验证（见 P0-1、P0-2、P0-3 的「实证」），并在 `PYTHONPYCACHEPREFIX=/tmp` 下做了一次 `py_compile` 语法检查（通过，未在仓库落 `__pycache__`）。文中行号均为工作区当前行号。
>
> 审查范围边界：`main.py`、`pages/*` 属其他审查分区，本报告只在"可利用性证据"意义上引用其行号，不在此下结论（个别跨区严重问题在「已排除 / 非问题」末条标注移交）。

## 汇总

| 级别 | 条数 | 主题 |
| --- | --- | --- |
| P0 | 6 | SQL 只读守卫可绕过、`user_version` 被改即自动重建空库、计数器与并发事务、任意文件读取、`role` 自我提权、`secret_key` 免密管理员凭据 |
| P1 | 11 | 每 rerun 4×PBKDF2、后台全表拉取、导入期副作用与路径漂移、注册竞态、举报状态无 CAS、草稿静默新建、`tags=None` 清空、孤立附件清理竞态、末位评论不一致、conftest 全局 chdir、config 读取需写权限 |
| P2 | 12 | 静态盐/明文附件密码、LIKE 未转义、分页无 clamp、举报无目标校验、标签无白名单、作者无校验、评论按楼层次序定位、返回值/异常约定、孤儿评论 N+1、本地时间无时区、导出无鉴权、死代码与失配的对账函数 |

---

## P0

### P0-1 `execute_sql` 的"只读守卫"可被 `WITH ... DELETE/UPDATE/INSERT` 绕过，且写入已生效后才抛 TypeError

- 位置：`api.py:85-92`（守卫 87-88、执行 90、取列 91）
- 代码：
  ```python
  stripped = (query or "").lstrip().rstrip(";").strip()
  head = stripped.split(None, 1)[0].upper() if stripped else ""
  if head not in ("SELECT", "WITH", "EXPLAIN", "PRAGMA"):
      raise ValueError("仅允许只读查询：SELECT / WITH / EXPLAIN / PRAGMA")
  cursor = db.execute_sql(stripped)
  columns = [col[0] for col in cursor.description]   # 写语句这里是 None
  ```
- 实证（sqlite3 3.49.1，独立内存库，未碰项目库）：`WITH x AS (SELECT 1) DELETE FROM _post` **执行成功**，`rowcount=-1`、`cursor.description is None`、表被清空。即 90 行写操作已提交，91 行才因 `None` 不可迭代抛 `TypeError: 'NoneType' object is not iterable`。
- 影响：后台"SQL 工具"（`pages/admin.py:475`，提示语称"写操作已被拦截"）实际可执行任意 `WITH` 前缀的写语句——一条 `WITH t AS (SELECT 1) DELETE FROM _post` 清空全站文章，一条 `WITH t AS (SELECT 1) UPDATE _user SET role='ADMIN'` 提权，且报错文案是 `TypeError`，管理员会误判为"查询语法问题"而重试。多语句注入本身被 `sqlite3` 拒绝（`ProgrammingError: You can only execute one statement at a time`），所以危险面就是单条 `WITH` 写语句与下一条 P0-2 的 PRAGMA。
- 建议：不要用首关键字黑名单，改用 SQLite authorizer 在**编译期**拒绝写操作（拿到底层连接：`conn = db.connection()`，`conn.set_authorizer(_deny_write_actions)`，对 `sqlite3.SQLITE_INSERT/UPDATE/DELETE/CREATE_*/DROP_*/ALTER_TABLE/REINDEX/ATTACH/DETACH/PRAGMA` 返回 `sqlite3.SQLITE_DENY`，其余 `SQLITE_OK`）；同时在执行前 `if not query.strip(): raise`，并保留 `head` 检查作为可读性提示。authorizer 生效后写语句在 prepare 阶段就失败，不会再出现"先毁库后报错"。另：`pages/admin.py` 的提示语要随实现一起改，避免继续宣称"已拦截"。

### P0-2 `PRAGMA` 白名单放行写型 PRAGMA：改 `user_version` 会在下次启动触发"归档 + 重建空库"

- 位置：`api.py:87-88`（放行 `PRAGMA`）；触发点 `api.py:653-655`、`616-629`；版本写回 `api.py:658-659`
- 代码：
  ```python
  # api.py:653-655
  legacy = db.table_exists("_post") and _current_user_version() != SCHEMA_VERSION
  if legacy:
      _archive_legacy_db()
  # api.py:621-623
  backup = f"{path}.legacy-{stamp}"
  if os.path.exists(path):
      os.replace(path, backup)
  ```
- 实证：`PRAGMA user_version=9` 执行成功（值已改变），`PRAGMA foreign_keys=OFF` 同样成功。二者都能通过 87 行的 `head == "PRAGMA"` 检查。
- 影响：三条真实损坏路径 ——(1) 管理员执行 `PRAGMA user_version = 0` 后，**下一次进程启动**（部署/重启/`streamlit run` 重拉）走到 653 行，判定为 v1 旧库 → `os.replace` 移走 `data.db` → `create_tables(safe=True)` 建出 10 张空表 → 全站数据"消失"（实际在 `.legacy-*` 里，但界面无任何提示，只有 stdout 一行 print）；(2) `PRAGMA foreign_keys=OFF` 在连接级关掉外键，之后所有 `on_delete="CASCADE"` 失效，删文章留下孤儿评论/点赞/附件记录（计数器与明细从此永久不一致）；(3) `PRAGMA journal_mode=WAL` 会把库切成 WAL 模式，与 `api.py:45` 注释里"刻意不开 WAL 以便后台直接读库文件备份"的前提冲突，且残留 `-wal`/`-shm` 文件。
- 建议：PRAGMA 走白名单**只读集合**（`table_info|table_list|table_xinfo|index_list|index_info|index_xinfo|foreign_key_list|database_list|compile_options|integrity_check|quick_check`，且正则要求 `PRAGMA <name>` 或 `PRAGMA <name>(...)`，**不含 `=`**）；`user_version` 与 `foreign_keys`/`journal_mode` 从白名单里永久剔除。同时把 legacy 判定从"版本号不等"改为"结构特征探测"（例如检查 `_meta` 表 + schema hash 或 `_report` 表是否存在），让 `user_version` 只作辅助信息，绝不再单独作为"移走用户数据"的依据。

### P0-3 点赞/收藏取消路径无条件 `-1`，且并发下 `OperationalError: database is locked` 未捕获 → 计数下溢、永久漂移、页面 500

- 位置：`api.py:1256-1261`（Like 取消）、`1296-1301`（Bookmark 取消）、`1267-1269`/`1305-1307`（异常捕获）、`46-49`（连接 pragma）、`1140-1152`（唯一的对账函数 `recount` 是死代码）
- 代码：
  ```python
  if like:
      like.delete_instance()
      _Post.update(like_count=_Post.like_count - 1).where(_Post.id == postid).execute()
      return False
  ```
- 实证（两连接，脚本在 /tmp）：并发读-改-写时，第二个连接拿到 `OperationalError: database is locked`，**第一个连接的 `COMMIT` 也报同一错误**（deferred 事务的锁升级死锁；`busy_timeout=5000` 只能缓解读锁争用，救不了 upgrade 死锁）。
- 影响：(1) `delete_instance()` 返回受影响行数但代码不看，与 P0-2 的 `foreign_keys=OFF` 叠加时，明细行早已被清理而计数仍 `-1` → `like_count` 可到负数（首页排序、`get_author_stats` 展示、`get_filtered_paginate(sort_by="likes")` 全部被污染）；(2) `except UNIQUE_VIOLATION` 只覆盖 `IntegrityError`，`OperationalError` 直接冒泡到 Streamlit → 用户看到红色异常页；(3) 全仓没有 `recount` 的调用方（`api.py:1140` 定义，`bench/seed.py:21` 只在注释里提），v1 里"计数不对就重算"的兜底在 v2 丢失。
- 建议：取消路径改成"只有真的删掉了才减一"，并且用 `MAX(x-1,0)` 兜底：
  ```python
  deleted = _Like.delete().where((_Like.post == postid) & (_Like.user_id == userid)).execute()
  if deleted:
      _Post.update(like_count=fn.MAX(_Post.like_count - 1, 0)).where(_Post.id == postid).execute()
  ```
  `_Bookmark` 同改。写路径用 `db.atomic("IMMEDIATE")`（peewee 的 `SqliteDatabase.begin(lock_type)` 支持，见 `peewee.py:4369`）把 deferred 事务升级为立即写锁，消除 upgrade 死锁；`Like/Bookmark.toggle` 的 `except` 扩成 `except UNIQUE_VIOLATION` + `except OperationalError: return False`（或重试一次），并给管理员后台接一个 `Post.recount()` 按钮，把 P0-3 产生的漂移做成可修复项而不是只能手改库。

### P0-4 `get_avatar_bytes` 对绝对路径直接 `open()` → 任意文件读取（可读 `.streamlit/secrets.toml`、`data.db`、任意盘符文件）

- 位置：`api.py:1563-1564`（`if os.path.isabs(avatar_name): path = avatar_name`）；写入端 `api.py:806-807`（`if avatar: user.avatar = avatar`，无任何校验）；渲染点 `pages/home.py:383`
- 代码：
  ```python
  if os.path.isabs(avatar_name):
      path = avatar_name
  elif avatar_name == "default_avatar.jpeg":
      path = os.path.join(DIR, avatar_name)
  else:
      path = os.path.join(AVATARS_DIR, os.path.basename(avatar_name))
  ```
- 影响：`User.modify` 只要求"本人密码正确"（798 行）就能把 `avatar` 字段写成任意字符串，而 `get_avatar_bytes` 对绝对路径**完全跳过 basename 防护**把文件内容读出来，经 `st.image` 渲染给任何打开该用户主页的访问者（`home.py:383` 在 `if user_id:` 分支，任何登录用户都能看别人主页）。组合起来：普通注册用户 → `modify(自己, 密码, avatar="C:/.../.streamlit/secrets.toml")` → 访问者打开其主页，页面把密钥文件以图片字节形式吐出（`st.image` 会失败但那串字节已进响应/日志；用 `.png` 后缀的真实敏感文件则直接渲染）。同类可读 `data.db`、`main.py`、任意用户目录文件。
- 建议：删掉 `os.path.isabs` 分支，统一 `path = os.path.join(AVATARS_DIR, os.path.basename(avatar_name))`；如确需兼容旧数据里的绝对路径，只在 `init` 时做一次性迁移（把旧绝对路径 `shutil.copy` 进 `AVATARS_DIR` 并改库），运行期绝不放行绝对路径。同时在 `User.modify` 里校验 `avatar`：必须是 `^[0-9a-f]{32}\.jpe?g$` 且 `os.path.isfile(os.path.join(AVATARS_DIR, avatar))`，否则拒绝或回落 `default_avatar.jpeg`。

### P0-5 `User.modify` 的 `role` 参数无授权边界：任何用户用自己的密码就能把自己改成 `ADMIN`

- 位置：`api.py:798`（鉴权）、`808-809`（`if role: user.role = role`）；对照 `api.py:723-728`（`check_admin` 如何判定管理员）
- 代码：
  ```python
  if self.check(userid, password) or self.check_admin(admin_secret_key):
      ...
      if role:
          user.role = role
  ```
- 影响：`role` 与 `username/description/avatar` 走同一个 `if`，鉴权条件只是"本人密码正确**或**管理员密钥正确"。任何注册用户 `User().modify(自己id, 自己密码, role="ADMIN")` 即成为管理员，随后 `check_admin` 通过 → 后台全部能力：导出全库用户/评论、`delete_comment_by_id` 删任意评论、`execute_sql`（叠加 P0-1）、`delete_orphaned_attachments` 删文件。当前 `pages/home.py:429-435` 没传 `role` 所以不是现存 UI 漏洞，但数据层是唯一权威边界，且 `pages/admin.py:179-186` 已经证明调用方会通过同一入口传 `role`——一旦有人复用 `modify` 或新增"编辑资料"页，就是一条提权路径。
- 建议：把 `role` 从 `modify` 签名里删掉，单开 `set_role(admin_secret_key, target_userid, role)`，函数体第一行强制 `if not self.check_admin(admin_secret_key): return False`，并校验 `role in (const.admin, const.user)`；`modify` 保持"只能改资料 + 密码"，且 `new_password` 之外不允许改任何权限字段。

### P0-6 `User.get_secret_key` 无鉴权 + `secret_key` 是永久免密管理员凭据（配合已入库的 `config.toml` 里的 admin userid 可直接组合成提权）

- 位置：`api.py:774-780`（无鉴权取密钥）、`api.py:726-728`（`_User.get_or_none(_User.secret_key == secret_key)` → 直接 `return user.role == admin`）、`api.py:152`（`secret_key` 无过期字段）；写入 Cookie 的调用点 `pages/login.py:33-40`、`pages/register.py:52-60`
- 代码：
  ```python
  @staticmethod
  def get_secret_key(userid: str) -> str | None:
      """获取用户密钥"""
      user = _User.get_or_none(_User.userid == userid)
      if user:
          return user.secret_key
  ```
- 影响：`secret_key` 是"知道即可当该用户"的长期凭据，`check_admin` 对它的唯一校验是"字符串相等且 role 是 ADMIN"，没有过期、没有撤销、没有审计；签发面却是一个**不需要密码**的静态方法：(1) 只要知道 userid（`config.toml` 已入库，含 6 字符 admin userid；`pages/admin.py:151` 的用户表也列出了所有 userid）就能取到对应密钥；(2) `pages/login.py:37` 与 `register.py:56` 把密钥写进 `cookies["secretkey"]` 长期保存，Cookie 的加密密码在 `main.py:56-59` 有公开兜底常量 `dev-insecure-fallback-change-me`——只要部署时没设 `COOKIES_PASSWORD`/secrets，任何人都能解密/篡改他人的 Cookie（此条属 main.py 分区，见文末移交）；(3) 密钥泄露后无法单点作废（只能改 `secret_key` 字段，但没有任何"重新签发"入口）。
- 建议：把 `get_secret_key` 改为私有 `_issue_secret_key(userid, password)`（校验密码通过后才签发）或直接从对外 API 删除，登录页改用 `login()` 的返回值携带密钥；`_User` 增加 `secret_key_expires_at` 与 `secret_key_revoked`，`check_admin` 增加"未过期 + 未撤销"判定；密钥入库存 hash（`hashlib.sha256(secret_key)` 即可，等值比对方便）而非明文；后台加"作废并重发"操作，用于密钥泄露后的止损。

---

## P1

### P1-1 每次 rerun 最多 4 次 PBKDF2 200k：单次交互白烧 ~450ms CPU

- 位置：`api2.py:23-25`；`api.py:591-602`、`579`（`PBKDF2_ITERATIONS = 200_000`）；调用点 `pages/home.py:241`、`671`、`720`、`main.py:166`
- 代码：
  ```python
  def check_by_state() -> bool:
      return user.check(state.get("userid", ""), state.get("password", ""))
  ```
- 影响：`check()` 每次都是完整 `verify_password` → `pbkdf2_hmac(200k)`。实测单次 **111.9 ms**，一次首页 rerun 内 4 处调用 = 约 0.45 s 纯 CPU（单用户单次交互），并发用户下会顶满 worker；而 `api2.py:8-12` 的 docstring 明确写了"继承自 v1 的教训：不要做会话内记忆化"，说明作者已知这是个坑，但现状仍是每次全量校验。
- 建议：登录时校验一次，把结果缓存在 `st.session_state["_auth"] = {"userid":..., "pw_digest": hmac_sha256(password, session_nonce)}`，`check_by_state()` 只比较 `state["userid"]` 与该摘要（**不缓存布尔值**，避免 v1 的"缓存 False"故障）；`password` 本身不再常驻 session（顺带解决明文密码长驻内存）。`check_admin`/`modify`/`delete` 的管理员路径保持每次 `check_admin`（管理员操作频率低，值得全验）。

### P1-2 后台图表区每次 rerun 全表拉取三张表（万级数据下后台首屏不可用）

- 位置：`pages/admin.py:76-78`（模块级，位于 `with st.expander("数据图表", expanded=True)`）；`pages/admin.py:231`（又一次 `pd.DataFrame(post.get_all())`）；数据层 `api.py:941-944`（`Post.get_all`）、`736-739`（`User.get_all`）、`1765-1790`（`get_comment_summary`）
- 代码：
  ```python
  posts_all = Post.get_all()          # 全表 + 每帖补 tags/attachments
  users_all = User.get_all()          # 全表全列（含 password/secret_key）
  comments_all = get_comment_summary()  # 全表评论 + Python 里算楼层次序
  ```
- 影响：`get_all()` 走 `_post_dicts`，对**每页所有帖子**再发 `_tag_map`/`_attachment_map` 两次查询（批量，但仍是全量正文，帖文多时就是整库正文进内存）；`get_comment_summary` 把全部评论拉进 Python 逐条造 dict 并顺序编号；`User.get_all` 甚至把 `password`/`secret_key` 全列读进内存再在 `admin.py:151-155` 丢弃。Streamlit 每次交互（点一下 tab、翻一页）都会重跑这些模块级语句。数据量到几千帖/几万评论时后台首屏就是几秒 + 数百 MB。
- 建议：图表所需只是聚合值，全部下推 SQL：发文量按日 `select strftime('%Y-%m-%d',created_at) d, count(*) from _post group by d`；用户角色分布 `select role,count(*) group by role`；评论数直接读 `_Post.comment_count` 求和（这正是冗余计数器的用途）。确实需要明细的地方加 `st.cache_data(ttl=60)` 并显式 `clear`，或挪到按钮后面（`if st.button("生成图表")`）。`get_comment_summary` 的楼层次序改用窗口函数 `ROW_NUMBER() OVER (PARTITION BY post ORDER BY created_at,id)`，避免把正文之外的排序工作搬进 Python。

### P1-3 导入期副作用 + 数据库路径与附件路径不一致（换 CWD 启动 = 看到空库、"备份"是空库）

- 位置：`api.py:46-49`（`SqliteDatabase("data.db", ...)` 相对 CWD）、`55-57`（`DIR = os.path.dirname(__file__)` 绝对）、`59-60`（import 时 `os.makedirs`）、`662`（`init_database()`）、`665-675`（建管理员 + 修密钥）、`1921`（发公告）；对照 `_db_path` `606-608`
- 代码：
  ```python
  db = SqliteDatabase("data.db", pragmas={"foreign_keys": 1, "busy_timeout": 5000})
  ATTACHMENTS_DIR = os.path.join(DIR, "attachments")
  ...
  init_database()            # api.py:662
  _admin = admin_credentials()  # 665
  bootstrap_announcements()  # 1921
  ```
- 影响：(1) 数据库按**进程 CWD** 解析，附件/头像按 `__file__` 解析 → 从别的目录启动（IDE 运行配置、systemd `WorkingDirectory` 缺失、`streamlit run /abs/path/main.py`、cron）会新建一个空 `data.db`，而附件目录仍指向项目里那份 → 用户看到"帖子全没了但附件还在"；`_archive_legacy_db` 的 `path` 也来自相对 `db.database`，备份文件会落在错误目录，管理员在项目根找不到"备份"。(2) 只要 `import api` 就建库、建管理员、扫全表修密钥、发公告、建两个目录：测试必须 `chdir` 规避（见 P1-10）、任何工具/脚本/IDE 索引一 import 就写盘。(3) `admin_credentials()` 走 `Config()`，`config.toml` 缺失时 560 行 `FileNotFoundError` 未被捕获 → 启动直接崩（见 P1-11）。
- 建议：路径统一到 `DIR`（`SqliteDatabase(os.environ.get("FREELAKE_DB") or os.path.join(DIR, "data.db"))`），把 `init_database()`/建管理员/`fix_duplicate_secret_keys`/`bootstrap_announcements` 收进显式 `def init(app_root: str | None = None)`，由 `main.py` 在入口调用一次；`os.makedirs` 同样移进去。保留"直接 `import api` 也能用"的兼容性可以给 `init()` 加 `reuse_if_open` 幂等保护，但不要在模块级执行。

### P1-4 注册的 `exists()` → `create()` 之间无竞态保护，且 `register` 把 `role` 开放给调用方

- 位置：`api.py:683-699`（`role: str = user` 参数 687、`if not self.exists(userid)` 689、`_User.create(...)` 690-696）；`exists` 实现 `731-734`
- 代码：
  ```python
  def register(self, userid, username, password, role: str = user) -> bool:
      if not self.exists(userid):
          _User.create(userid=userid, ..., role=role, ...)
          return True
      return False
  ```
- 影响：(1) 两个请求同时注册同一 userid 时都通过 689 行的检查，第二个 `create` 抛 `IntegrityError`（primary key 冲突）→ 未捕获，注册页 500；`UNIQUE_VIOLATION` 元组就定义在 `api.py:51` 却没用在这里。(2) `role` 参数一路写进库，没有白名单也没有"仅管理员可传"的门槛——`register("x","x","x", role="ADMIN")` 直接造管理员；P0-5 是修改路径，这里是创建路径，同一个洞的两个入口。(3) `exists()` 返回的是 `_User` 模型实例（731-734），`assert user_api.exists(...)` 靠真值判断能过，但 `if user.exists(x) is True` 恒假。
- 建议：删掉 `role` 参数，固定 `role=const.user`；`create` 包 `try/except UNIQUE_VIOLATION: return False`，把 `exists()` 降级为可选的快速路径（真正保证唯一性的是主键约束）；`exists()` 改 `return _User.get_or_none(...) is not None`（或 `select().where().exists()`）。

### P1-5 举报处理无 `status='pending'` 条件：已处理/已驳回的举报可被反复改写

- 位置：`api.py:1407-1422`（`handle`），关键 1412-1421
- 代码：
  ```python
  updated = _Report.update(status=action, handled_by=handled_by,
                           handled_at=now_str(), note=(note or "")[:500]) \
                   .where(_Report.id == report_id).execute()
  return updated > 0
  ```
- 影响：`WHERE` 只有 id，没有 `status == "pending"`。管理员重复点"已处理"会把 `handled_at`/`handled_by`/`note` 刷成最后一次操作者（审计链断裂）；两个管理员同时处理同一举报时后写覆盖前写，且都返回 True 让 UI 显示"成功"；"驳回"可以被改成"已处理"，历史结论被静默篡改。`_Report.status` 有 `index`（`api.py:293` 附近）且 `index(status,id)` 就是为了列表查询，加 CAS 条件不额外付代价。
- 建议：`.where((_Report.id == report_id) & (_Report.status == "pending"))`，`updated == 0` 时区分"不存在"与"已被处理"（先 `get` 一次）并返回明确结果（建议返回 `str`/枚举：`ok|not_found|not_pending`，让 UI 提示"该举报已被 xxx 处理"）；`handled_at` 由服务端 `now_str()` 生成已经是对的，不要放开给调用方。

### P1-6 `Draft.save` 在 `draft_id` 不属于本人时静默新建，且 `tags`/`attpassword` 用默认值覆盖已有草稿

- 位置：`api.py:1441-1461`（`draft_id` 分支 1442-1452，落空后 `return _Draft.create(...)` 1453-1461）
- 代码：
  ```python
  if draft_id:
      d = _Draft.get_or_none((_Draft.id == draft_id) & (_Draft.user_id == userid))
      if d:
          ...
          d.tags = tags or []
          d.attpassword = attpassword
          d.save()
          return d.id
  return _Draft.create(...).id
  ```
- 影响：(1) 用户 A 传别人的 `draft_id`（或已删除的 id）时不报错，而是**新建一条草稿**，返回值仍是个合法 id，调用方（`pages/publish.py:127-147`）以为"更新成功"，实际草稿箱里多一条、且 `editing_draft_id` 指向新 id，下次编辑又新建一条——草稿无限增长且原文留在旧记录里；(2) `d.tags = tags or []` / `d.attpassword = attpassword`：调用方只想改标题时（`tags` 默认 `None`、`attpassword` 默认 `""`）会把已有草稿的标签清空、附件密码清成公开；(3) `delete`/`get_draft` 都做了本人校验（1474-1493），只有 `save` 漏了，API 语义不一致。
- 建议：`draft_id` 给了但查不到 → 返回 `None`（或抛 `PermissionError`），由 UI 提示"草稿不存在或无权修改"，绝不回落新建；新建走显式 `draft_id=None`。`tags`/`attpassword` 用哨兵值（`_UNSET = object()` 或 `tags: list[str] | None = None` 表示不改、`[]` 表示清空），并在更新分支里只在"显式提供"时赋值。

### P1-7 `Post.edit(tags=None)` 会清空文章全部标签（默认值与"不改"语义相反）

- 位置：`api.py:919-931`（默认 `tags: typing.List[str] = None` 920；`_sync_tags(postid, tags)` 930）；`_sync_tags` 的"先全删"在 `api.py:847`
- 代码：
  ```python
  def edit(postid, title, content, tags: typing.List[str] = None) -> bool:
      with db.atomic():
          updated = _Post.update(title=title, content=content).where(_Post.id == postid).execute()
          if not updated:
              return False
          _sync_tags(postid, tags)      # tags=None → 删光所有标签
  ```
- 影响：任何"只改标题/正文"的调用方（将来加"编辑正文"页、脚本批量改题、管理员修正内容）不传 `tags` 就会把该帖标签全部删掉，且不可恢复（`_PostTag` 被物理删除，`_Tag` 里只剩孤立标签）。当前唯一调用方 `pages/home.py:148` 传了 `new_tags`，所以是"下一个调用者会踩的坑"，不是现存数据损坏。
- 建议：`tags` 默认值改为哨兵 `_UNSET`；`if tags is not _UNSET: _sync_tags(postid, tags)`，`tags=[]` 明确表示清空。`Draft.save`（P1-6）同改。`_sync_tags` 内部把"先全删再重建"改成 diff（新增缺失、删除多余的）也能顺带减少无谓的 delete/insert 与 `_Tag` 抖动。

### P1-8 `delete_orphaned_attachments` 与在途上传存在竞态：后台一点清理就删掉用户刚上传的文件

- 位置：`api.py:1757-1762`（清理）、`1743-1754`（判定孤立）；上传先落盘后入库在 `api.py:1176-1177` 与 `1180-1186`（`save` 返回 meta），入库发生在 `Post.publish` → `_sync_attachments`（`api.py:906`、`853-866`）
- 代码：
  ```python
  def delete_orphaned_attachments() -> int:
      orphans = get_orphaned_attachments()   # 只比对 _attachment.stored_name
      for f in orphans:
          os.remove(os.path.join(ATTACHMENTS_DIR, f))
  ```
- 影响：`Attachment.save` 落盘（1176）到 `_Post.create` 事务提交之间存在窗口（用户还在填标题、或发布事务排队等锁）。这期间文件在磁盘上但库里没有对应 `stored_name` → 被判为孤立 → 管理员在后台点"清理孤儿附件"就把它 `os.remove` 掉，用户提交时 `_sync_attachments` 插入成功但文件已不存在，之后 `get_file` 返回 `b""`（`1236-1239`），帖子附件永久 404，且没有任何错误提示。
- 建议：孤立判定加时间阈值（`os.path.getmtime(f) < time.time() - 3600` 才算孤立，给上传-发布留足窗口），或在 `Attachment.save` 里先写一条 `_Attachment(post_id=None, stored_name=...)` 占位（需要把 `post` 改为可空），发布事务里 UPDATE 绑上；清理前先 `db.atomic()` 里重新对比一次并只删"确认无引用且足够旧"的文件。

### P1-9 "最后一条评论"用 `MAX(id)`，与正文评论列表的 `(created_at, id)` 排序不一致

- 位置：`api.py:466-476`（`_last_comment_map`，470-475 `fn.MAX(_Comment.id)` 子查询）；对照 `api.py:452-457`（`_comments_map` 用 `order_by(post, created_at, id)`）
- 代码：
  ```python
  sub = (_Comment.select(fn.MAX(_Comment.id).alias("cid"))
         .where(_Comment.post.in_(list(post_ids))).group_by(_Comment.post))
  rows = _Comment.select().where(_Comment.id.in_(sub)).dicts()
  ```
- 影响：正常写入路径下 `id` 与 `created_at` 同序，两个函数结果一致；但一旦出现时间回填（迁移/种子数据/管理员改系统时间后补发，`bench/seed.py` 就有这类写入），`id` 最大 ≠ `created_at` 最大 → 首页卡片显示的"最后一条评论"和进入帖子后评论区的最后一条不是同一条，用户看到"卡片上有这句话、点进去找不到"。`get` 的 `comments` 与 `last_comment` 由两套排序各自生成，属于同一契约的两种口径。
- 建议：`_last_comment_map` 改成和 `_comments_map` 同一个排序口径：`ROW_NUMBER() OVER (PARTITION BY post ORDER BY created_at DESC, id DESC) = 1`（peewee 里用 `_Comment.select(...).where(_Comment.id.in_(sub))` 的子查询改成带 `ORDER BY` 的 `MAX` 复合键，或干脆复用 `_comments_map` 的最后一项）。最省事且零风险的做法：让 `_post_dicts` 在需要 `last_comment` 时对已取到的 `comments` 列表取末位（`json.loads(comments[-1])`），彻底去掉第二套排序。

### P1-10 `tests/conftest.py` 在导入期 `os.chdir` 到临时目录且不恢复：Windows 下临时目录必然泄漏，且测试行为依赖 CWD 全局状态

- 位置：`tests/conftest.py:26-30`（`mkdtemp` 26、写 config 27-28、`os.chdir` 29、`atexit` 清理 30）；清理 fixture `33-41`
- 代码：
  ```python
  _TMP_ROOT = tempfile.mkdtemp(prefix="freelake_test_")
  os.chdir(_TMP_ROOT)
  atexit.register(lambda: shutil.rmtree(_TMP_ROOT, ignore_errors=True))
  ```
- 影响：(1) `atexit` 执行时进程 CWD 仍在 `_TMP_ROOT` 内，Windows 不允许删除当前工作目录 → `rmtree(ignore_errors=True)` 静默失败，每跑一次测试就在 `%TEMP%` 留一个 `freelake_test_*` 目录（含一个 `data.db` 与 `config.toml`）；(2) `chdir` 是进程全局状态，pytest-xdist/其他插件、以及任何"相对路径读仓库文件"的测试都会在错误目录下工作；(3) `_clean_db` 逐表 `delete().execute()` 但不重置 `sqlite_sequence`，跨用例的 id 单调递增，这也是 P2-12 里几条硬编码断言脆弱的原因之一；(4) `tmp_dirs`（44-53）只改模块常量，没清 `st.cache_data`（`api.py:1189` 的缩略图缓存以 `saved_name` 为键，两次不同 tmp 目录下同名文件会串缓存）。
- 建议：把 `chdir` 从模块导入期挪进 `@pytest.fixture(scope="session", autouse=True)`，用 `monkeypatch.chdir`（自动恢复），并在 `atexit`/fixture 结束时先 `os.chdir(PROJECT_ROOT)` 再 `rmtree`；`_clean_db` 里补 `db.execute_sql("DELETE FROM sqlite_sequence")`（仅当表存在）；`tmp_dirs` 里调 `api.get_thumbnail_bytes.cache_clear()`（`st.cache_data` 包装后的对象带 `clear`）或干脆在测试中禁用缓存。

### P1-11 `Config.load()` 用 `"r+"` 打开 `config.toml`：只读部署直接崩溃；文件缺失也崩

- 位置：`api.py:524-526`（`with open("config.toml", "r+", encoding="utf-8")`）；调用链 `533-560`（`admin_credentials`）→ `665`（导入期）；缺失分支 `560`（`Config().config.get("admin", {})`）
- 代码：
  ```python
  def load(self):
      with open("config.toml", "r+", encoding="utf-8") as f:
          self.config = toml.load(f)
  ```
- 影响：(1) 读取只需要读权限，`r+` 却要求写权限——容器/只读挂载/权限收紧的部署上，`import api` 直接 `PermissionError`，站点起不来，而错误信息指向 `config.toml` 与"密码配置"毫无关系，排查成本极高；(2) `config.toml` 不在 CWD（P1-3 的路径问题）时 `FileNotFoundError` 从 525 行冒到 560 行的调用处，`admin_credentials` 没有 try/except，同样是启动期硬崩溃；(3) 兼作"配置即密码来源"的话，`config.toml` 已入库（`git ls-files` 可见），一旦本地填了真密码就会随提交泄露。
- 建议：`open(path, "r", ...)` 读取；`admin_credentials` 里把配置读取包进 `try/except OSError: return {}`（缺文件=没配置管理员，属正常情况）；`Config.load` 接受显式路径参数，默认 `os.path.join(DIR, "config.toml")`；在 `docs/` 或 `config.toml` 注释里明确"密码只允许来自 secrets/环境变量，本文件仅供 userid/username"。

---

## P2

### P2-1 附件密码用静态盐单次 sha256，草稿里还存明文

- 位置：`api.py:52`（`salt = "freelake"`）、`574-576`（`sha256(value + salt)`）、`904`（`attpassword=sha256(attpassword) if attpassword else ""`）；明文存储 `_Draft.attpassword`（模型 `303-321`，赋值 `api.py:1449`）
- 影响：`sha256(明文 + 固定盐)` 是单轮无迭代构造，GPU 上一秒可尝试上亿口令，附件密码通常是短口令 → 拿到库文件即可还原。`_Draft` 里的 `attpassword` 是**明文**，草稿表一旦被导出/备份泄露，等价于泄露附件密码本身。用户主密码已经用 PBKDF2（582-588），同一系统两套强度不一的策略。
- 建议：附件密码改 `hash_password()`（PBKDF2，与用户密码同格式，`verify_password` 直接复用）；`attpassword` 列在 v2 里保持 256 长度足够。`_Draft.attpassword` 改存 hash 或干脆不存（发布时再输入）。若担心历史数据，`verify_password` 已兼容旧格式，可在校验成功时"顺手升级"（与 `User.login` 707-709 的旧哈希升级同一手法）。

### P2-2 关键词搜索未转义 `%`/`_`：输入一个 `%` 返回全表

- 位置：`api.py:963-966`（`_Post.title.contains(keyword) | _Post.content.contains(keyword)`）；作者搜索同类问题 `976-987`（`_User.userid.contains` / `username.contains`）
- 影响：`contains` 生成 `LIKE '%' || ? || '%'`，用户输入 `%` 或 `_` 就变成通配符 → 搜 `%` 命中全部文章（配合分页可当"全量导出"用），搜 `_` 命中所有单字符位置，结果与用户预期不符且查询代价从索引退化到全表扫描。
- 建议：转义后再 `contains`：`kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")` 并加 `ESCAPE '\\'`（peewee 可用 `fn.LIKE` 自定义或 `.contains(escaped)` 配合 `_Post.title.concat` 的 `ESCAPE` 子句）；`_apply_filters` 里对 `tag`/`author` 同样处理。

### P2-3 分页参数无 clamp：`page<=0`、`page_size` 巨大都能直接落到 SQL

- 位置：`api.py:1043`（`query.order_by(*order_by).paginate(page, page_size).dicts()`）
- 影响：`page=0` 时 peewee 的 offset 为负，SQLite 把负 offset 当 0（静默返回第一页，前端页码却显示 0）；`page_size=100000` 时一次拉全库正文（管理员/爬虫可用 `?page_size=` 把内存打满）；`page` 传字符串会 `TypeError` 冒泡成 500。页面当前把 `page` 交给 `st.number_input` 限制，但 URL/`st.query_params` 路径可绕过。
- 建议：入口做 `page = max(1, int(page or 1))`、`page_size = min(max(1, int(page_size or 5)), 50)`，并对非法类型返回 `ValueError` 由调用方转成友好提示。

### P2-4 `Report.add` 允许 `postid`/`commentid` 同时为空，也允许指向不存在的目标：无法去重的"空气举报"

- 位置：`api.py:1371-1380`（create，无目标校验）；去重依赖 `api.py:632-647` 的两个 partial unique index（`WHERE comment_id IS NULL` / `IS NOT NULL`，两列都 NULL 时两个索引都不生效）
- 影响：`Report.add("a", postid=None, commentid=None, ...)` 造出的举报既不属于帖子也不属于评论，且两个 partial index 都匹配不上 → 同一用户可无限重复提交，后台列表里是永远无法定位目标的垃圾行；同理 `postid=999999` 不校验存在性，举报指向不存在的文章，管理员点"查看"得到 404。
- 建议：入口校验 `if (postid is None) == (commentid is None): return None`（必须且只能指定一个），并校验目标存在（`_Post.get_or_none` / `_Comment.get_or_none`）；补一个覆盖"两列都空"的 CHECK 约束比在 Python 里拦更可靠。

### P2-5 `_Tag` 无白名单、无回收：任意标签名可入库并永久驻留

- 位置：`api.py:847-850`（`_Tag.get_or_create(name=name)`）；`const.py:8-28` 定义了 18 个合法标签，`const.py:35` 的 `announcement_tags` 里有 `迎新`/`社区规范` 不在 18 个里
- 影响：数据层不校验标签是否属于 `const.tags`（UI 的 `accept_new_options=False` 只是界面约束），任何调用方/脚本可直接写入任意字符串；`_sync_tags` 先 `_PostTag.delete()` 再重建，`_Tag` 行永不清除 → 标签表随编辑历史单调膨胀，后台标签筛选下拉会出现大量无文章引用的僵尸标签；`const.py` 内部就已经存在"公告标签不在 tags 列表"的不一致。
- 建议：`_sync_tags` 里对不在允许集合的标签直接丢弃（允许集合 = `const.tags + const.announcement_tags`，公告标签由 `bootstrap_announcements` 显式放行）；后台加"清理无引用标签"（`_Tag.delete().where(_Tag.id.not_in(_PostTag.select(_PostTag.tag)))`）。

### P2-6 `Post.publish` 不校验 `authorid` 存在：逻辑外键孤儿（测试自己就在用不存在的作者）

- 位置：`api.py:899-903`（`_Post.create(author_id=authorid, ...)`）；证据 `tests/test_data_layer.py:310`（`Post.publish("someoneelse", ...)`）
- 影响：`author_id` 是 `CharField` 无 FK，任意字符串都能落库 → 首页 `authorid` 链接指向不存在的用户（`home.py` 的用户主页分支 `get_config` 返回 None，页面空白）、`get_author_stats` 查不到、导出 CSV 里出现幽灵作者。测试用例明确把这种数据当合法输入写进库，说明约束缺口已被"接受"。
- 建议：`publish` 开头 `if _User.get_or_none(_User.userid == authorid) is None: raise ValueError("作者不存在")`（或在 `_Post` 加 `Check` 之外的触发器）；同时把 `test_get_by_author` 改成"作者不存在时抛错"的负向用例，把幽灵作者从合法输入里去掉。

### P2-7 评论按"楼层次序索引"定位：索引漂移会删错人，且数据层无归属校验

- 位置：`api.py:869-880`（`_comment_id_at` 按 `(created_at,id)` 偏移取 id）、`1116-1122`（`edit_comment`）、`1124-1136`（`delete_comment`）；调用方 `pages/home.py:164`、`692-696`（`Post.delete_comment(int(post_id), index)`）
- 影响：(1) 索引在"渲染时"生成、"点击时"才消费；这期间只要有人删了更早的一条评论，`index` 就指向另一条 → 用户删掉的是别人的评论（同帖多用户并发时概率不低）；(2) `delete_comment`/`edit_comment` 不接收 `userid`，归属校验完全靠 UI 的 `can_manage`（`home.py:673-677`），数据层对"谁在改"零校验，任何新调用方（导出工具、脚本、将来的"批量清理"）都能删任意评论。`delete_comment_by_id`（1793-1804）反而按 id，两条路径语义不一致。
- 建议：对外接口改成按 `comment_id`（首页渲染时每个评论 dict 里已有 `id`，`_comment_dict` 362-370 保留），`edit_comment(comment_id, userid, content)`/`delete_comment(comment_id, userid)` 内部用 `WHERE id=? AND user_id=?`（或 `or user.role==ADMIN`）做 CAS，返回"无权/不存在/成功"三态；`_comment_id_at` 只留给"从 UI 第 N 楼跳转"这种只读场景。

### P2-8 返回值/异常约定不一致：`exists` 返回模型、`delete` 吞异常、`save_avatar` 吞异常、`execute_sql` 注解与实际不符且同名列丢失

- 位置：`api.py:731-734`（`-> bool` 实返 `_User`）、`821-837`（`except Exception: return False` 836）、`1538-1539`（`except Exception: pass`）、`79-92`（`-> list[dict[str, str]]`，PRAGMA 返回 int；`dict(zip(columns,row))` 同名列后者覆盖前者）
- 影响：类型检查器与读者都被误导；`User.delete` 把"数据库锁/外键错误"和"用户不存在"都变成 `False`，管理员看到"删除失败"却无法区分原因；`save_avatar` 对非图片输入静默原样保存并命名 `.jpg`（后续 `st.image` 解码失败、缩略图生成也失败），用户以为上传成功；`execute_sql("SELECT 1 AS x, 2 AS x")` 静默只留一个键，管理员会以为查询结果不全。
- 建议：`exists` 改 `return ... is not None`；`delete` 的 `except` 收窄到 `(OperationalError, UNIQUE_VIOLATION)` 并 `print`/返回错误码；`save_avatar` 的 `except` 至少 `save_avatar.last_error = exc` 或直接抛给 UI；`execute_sql` 的返回类型改 `list[dict[str, object]]`，列名重名时加后缀（`_2`）或在有重名时抛 `ValueError`。

### P2-9 `delete_orphaned_comments` 逐条 `DELETE`（N+1），且与 `get_orphaned_comments` 重复扫描

- 位置：`api.py:1835-1845`（取 id 列表后 `for cid in ids: _Comment.delete().where(...).execute()`）
- 影响：孤儿评论通常成百上千（删文后残留），每条一次 statement + journal 写，Windows 上单条 fsync 可能几毫秒 → 清理按钮卡住数秒；中途异常会留下"删了一半"的状态，返回值不反映部分成功。
- 建议：一条 `_Comment.delete().where(_Comment.post_id.not_in(_Post.select(_Post.id))).execute()` 直接删，用返回值当计数；外面包 `db.atomic()`。

### P2-10 `now_str()` 用本地时间且无时区：服务器时区变化会让排序/日期边界错乱

- 位置：`api.py:131-133`（`datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")`）、`695`（注册时手工拼同格式）、`128`（`NOW_FORMAT`）
- 影响：`created_at` 是"无时区的本地时间字符串"，排序与日期过滤（`988-995` 拼 `"00:00:00"`/`"23:59:59"` 做字符串比较）都依赖"同一台机器同一时区"。服务器从 UTC 换成 Asia/Shanghai（或夏令时地区）后，新帖时间戳比旧帖"早 8 小时"，新帖排到列表尾部；备份到另一时区恢复后历史时间整体偏移；跨时区用户看到的"今天"也不一致。
- 建议：统一存 UTC ISO8601（`datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")`），展示层再转本地；若为兼容 v1 数据必须保留 `"%Y-%m-%d %H:%M:%S"`，至少把 `now_str` 加时区注释并在 `_apply_filters` 的日期换算里显式用同一时区（`zoneinfo`），并在升级脚本里记录"历史时间按 X 时区解释"。

### P2-11 导出函数（users/posts/comments 的 csv/json，共 5 个）在数据层无任何鉴权

- 位置：`api.py:1581-1698`（`export_users_csv/json` 1581-1617、`export_posts_csv/json` 1620-1670、`export_comments_*` 1673-1698）
- 影响：它们是普通模块级函数，不校验调用者、不接收凭据，任何 `import api` 的脚本/新页面/未来 API 路由一调就得到全库用户资料（含 avatar、注册时间）与全部评论正文。当前唯一调用点是 `pages/admin.py`（有登录门禁），但"数据层无授权"意味着保护完全依赖调用点自觉——`export_users_json` 的注释（1596-1600）说明作者认真剔除了 password/secret_key，说明这类函数会被认真使用，更需要一道强制门。
- 建议：统一加 `admin_secret_key: str` 参数并 `if not User.check_admin(admin_secret_key): raise PermissionError`，或在导出的每个函数里读 `st.session_state` 校验 role（更方便但把数据层绑到 Streamlit，不推荐）；调用方 `admin.py` 相应传 `state["secretkey"]`。

### P2-12 死代码与失配：`_user_dict`、`Post.recount`、`get_thumbnail_base64`、`get_configs` 无调用方；唯一能修计数漂移的函数被弃用

- 位置：`api.py:348-359`（`_user_dict`，全仓只有 `api.py:113` 注释提到它）、`1140-1152`（`Post.recount`，全仓仅定义处命中）、`1213-1217`（`get_thumbnail_base64`）、`756-772`（`get_configs`）
- 影响：`_user_dict` 是 v1→v2 适配层的遗留半成品，留着会让后续维护者以为 `User` 的 dict 形状还有第二个来源；`recount` 正是 P0-3 所述计数漂移的唯一修复工具，却没有入口（后台既没有按钮也没有页面调用），等于出了问题只能手写 SQL；`get_configs`/`get_thumbnail_base64` 是没人用的便利函数，增加测试负担与误用面。
- 建议：删除 `_user_dict`/`get_thumbnail_base64`/`get_configs`（或明确标注 `# TODO: v3 移除`）；保留 `recount` 但在 `pages/admin.py` 的"数据维护"里加"重算计数"按钮（对单篇或全量：`_Post.update(like_count=... )` 用子查询），让它成为可运维的能力。

---

## 测试覆盖缺口

现有 25 个用例覆盖了"正常路径 + 少量已修 bug 的回归"（含 v1 的 sha256 升级、评论越界、附件穿越、草稿跨用户读删），但下列关键路径**完全没有用例**，且其中数条直接对应上面的 P0：

1. **`execute_sql` 的绕过面**（对应 P0-1/P0-2）：`tests/test_data_layer.py:566-579` 只测了 `DELETE/UPDATE/DROP/INSERT/""` 五个关键字前缀，且 570 行 `assert execute_sql("  select count(*) as n from _user  ;") is not None` 是空洞断言。缺 `WITH x AS (SELECT 1) DELETE FROM _user`、`PRAGMA user_version = 0`、`PRAGMA foreign_keys = OFF`、`EXPLAIN` 前缀写语句、多语句（`SELECT 1; DROP TABLE _post`）五类输入。
2. **v2 归档/重建路径**（对应 P0-2）：`init_database` 的 `legacy=True` 分支、`_archive_legacy_db` 的 `os.replace`、重建后表是否为空——零覆盖。也就是说"旧库改名后重建"这条最危险的分支从未在任何测试里跑过。
3. **计数器的取消路径与并发**（对应 P0-3）：`test_like_and_bookmark_toggle`（158-172）只走单线程顺序调用；缺"明细被直接删除后再 toggle"（计数下溢）、"两线程交替 toggle"（`OperationalError`）、以及 `delete_comment`/`delete_comment_by_id`/`Post.delete` 之后计数与明细的一致性断言。
4. **鉴权边界**（对应 P0-4/P0-5/P0-6/P1-4）：无一条用例测 `User.modify(role=...)`、`get_secret_key`、`check_admin` 的三个分支（密码路径 723、admin 密钥 726、错误密钥）、`register(role="ADMIN")`、`delete` 他人账号、`edit_comment`/`delete_comment` 他人评论。
5. **`get_avatar_bytes` 的输入校验**：`test_avatar_compression`（347-371）只测"上传→压缩"，没有测 `avatar="C:/x.png"` 绝对路径、`avatar="../../config.toml"`、`avatar` 为空/None 三种输入。
6. **评论按索引定位的漂移**：没有"删掉第 0 楼后按旧 index=1 删除"的用例——这正是 P2-7 的核心风险。
7. **草稿越权写**：`test_draft_save_load_delete`（497-519）测了跨用户"读/删"（514-517），但没测 `Draft.save(userA, draft_id=B的草稿)` 的行为（P1-6），也没测"只改标题时 tags/attpassword 是否被清空"。
8. **`Post.edit(tags=None)`**：`test_post_edit_and_delete`（121-129）每次传 tags，缺"不传 tags 后标签应保持"的断言（P1-7）。
9. **举报的去重维度**（对应 P2-4/P1-5）：`test_report_add_and_deduplicate`（377-412）没测"postid 与 commentid 都为空"、报同一文章不同的两评论被视作不同（522 有专门的 `test_report_post_and_comment_are_distinct`，但缺"双空"与"目标不存在"）、以及 `handle` 对已 `handled` 报告的二次调用（P1-5）——`test_report_handle_and_status`（415-431）只 handle 一次。
10. **分页/日期边界**（对应 P2-3）：`test_count_filtered_and_paginate_consistent`（188-225）只用 `page_size=1` 的 1/2 页；缺 `page=0`/负数/`page_size=100000`、缺"文章时间正好 00:00:00 与 23:59:59 两名边界值是否被包含"（`test_count_filtered_by_date_range` 228-261 用的是中间时刻）。
11. **事务回滚**：没有"附件 stored_name 冲突 / 标签写入失败 → `_Post` 是否回滚"的用例（`_sync_attachments` 的 unique 冲突会炸整个 publish 事务，但从未被验证）。
12. **`export_*` 五个函数**：零用例。`get_comment_summary`/`search_comments`/`get_orphaned_*`/`delete_orphaned_*` 也是零用例——后台管理主要功能没有数据层测试。
13. **管理员引导**：`bootstrap_announcements`（1855-1918）与 `fix_duplicate_secret_keys`（563-571）零用例，而两者都在 `import` 期执行，一旦出错是"应用起不来"级别。

### 脆弱断言清单（依赖具体数量/实现细节/顺序兜底）

| 位置 | 断言 | 问题 |
| --- | --- | --- |
| `tests/test_data_layer.py:198` | `assert Post.count_filtered() == 4` | 硬编码全局文章数，任何新用例或 fixture 顺序变化即挂；应用"本用例发布了几篇"的相对断言 |
| `tests/test_data_layer.py:284-287` | `date_sorted == sorted([p1,p2,p3], reverse=True)` | 三条帖子 `created_at` 同秒（`now_str` 无毫秒），排序实际靠 `id DESC` 兜住，对 `created_at` 排序零证明力；应显式造不同时间（像 240-245 那样白盒改 `created_at`）再断言 |
| `tests/test_data_layer.py:316` | `got[0]["id"] > got[-1]["id"]` | 同上，靠 id 单调而非 `created_at`；且整个用例建立在 `publish("someoneelse")` 这种非法作者数据之上 |
| `tests/test_data_layer.py:326` | `re.fullmatch(r"[0-9a-f]{32}\.txt", meta["saved_name"])` | 把"文件名 = uuid4().hex + 原扩展名"的内部实现钉死；改成"不含路径分隔符 + 扩展名保持 + 长度合理"即可验安全属性 |
| `tests/test_data_layer.py:570` | `assert execute_sql(...) is not None` | 空洞断言，任何返回都过 |
| `tests/test_data_layer.py:412` | `Report.count("pending") == 2` | 依赖 `_clean_db` 清理干净且别的用例不插 pending；应断言"本用例新增的条数" |
| `tests/test_data_layer.py:9` | docstring「数据库为 conftest 提供的内存库」 | 实际是临时目录里的**文件库**（`conftest.py:26-29`），错误文档会误导后来者以为可以随便并发/丢弃 |

---

## 已排除 / 非问题

以下经过核查后判定**不是**缺陷，或不属于本次数据层范围，列出来避免重复讨论：

1. **`comments` 是"JSON 字符串列表"**（`api.py:448-449`、`460-462`；`1079-1080` 注释）——这是 v1 契约的刻意保留（页面按 v1 形状消费），不是序列化失误，不列缺陷。
2. **后台用户表列了 `password`/`secret_key`**（`pages/admin.py:151-155`）——拿到 DataFrame 后立刻 `df.drop(columns=["password","secret_key"])`，不构成泄露；真正的风险在 P0-6 的 `get_secret_key` 与 Cookie 生命周期。
3. **`get_file` 的目录穿越防护是有效的**（`api.py:1231` `os.path.basename`），`tests/test_data_layer.py:330-331` 也有覆盖；缺陷只在 `get_avatar_bytes`（P0-4）这一处不一致。
4. **`on_delete="CASCADE"` 的层级设计**（`_Comment`/`_Like`/`_Bookmark`/`_Attachment`/`_PostTag` → `_Post`）与 v2 的 `foreign_keys=1` 配合是正确的；问题只在于 `PRAGMA foreign_keys=OFF` 能把这条链一次性关掉（P0-2），以及逻辑外键（作者/举报人）本就没有 FK。
5. **`UNIQUE_VIOLATION = (IntegrityError, sqlite3.IntegrityError)`**（`api.py:51`）同时捕获两层异常是对的（peewee 与 sqlite3 各自会抛），不列缺陷；只是它被用得过宽（把 FK/CHECK 冲突也当"重复举报/文章已删"，见 P2-4 与 `1267-1269`）。
6. **冗余计数器与 JSON 列**：按任务背景，`like_count/bookmark_count/comment_count` 与 `tags` 的 JSONField（草稿）是 v2 的既定设计，不建议再"拆 JSON 列/加索引"；本报告只讨论它们的**一致性维护**（P0-3、P1-2）。
7. **`test_avatar_compression`（347-371）用双重循环 `putpixel` 造 2000×3000 图**——只是测试慢（Windows 上约数秒），不影响正确性，不列缺陷；若在意 CI 时间可换 `Image.new(...)` + `ImageDraw.rectangle`。
8. **`v1/` 目录**是旧版副本，按任务要求不在审查范围。
9. **`Post.edit` 清空标签**在现有唯一调用方（`pages/home.py:148`）里传了 tags，因此**不是现存的数据损坏**，按"下一个调用者会踩坑"记 P1-7。
10. **跨区移交（不属于 api.py/const.py/api2.py/tests）**：`main.py:56-59` 的 Cookie 加密密码兜底 `dev-insecure-fallback-change-me` 是公开常量，配合 `main.py:14-16` docstring 承认的"userid + 明文密码存入 Cookie"，构成"默认部署下 Cookie 可被解密/伪造"的高危面；它与 P0-6 的 `secret_key` 互相放大，但修复点在 `main.py` 与部署配置，请页面/入口审查分区确认并合并结论。另：`pages/admin.py:76-78` 的模块级全表拉取（P1-2）作为数据层缺陷记录，但页面侧改法（缓存/懒加载）属 admin 页面分区。
