# AGENT.md · FreeLake 交接文档

> 本文件是给下一个接手此项目的 AI Agent / 开发者的交接说明。
> 目标：**读完本文件即可在不破坏现有约定的前提下改代码**。
> 最后更新：2026-10-25（commit `37f9f48`，分支 `master`）

---

## 1. 项目是什么

**FreeLake（自由论坛）**：基于 **Streamlit** 的校园论坛 Web 应用，单进程、无前端框架、无后端服务，
数据层用 **peewee + SQLite**（`data.db`），附件/头像落本地磁盘。

- 形态：Streamlit 多页应用（`st.navigation`），页面文件在 `pages/`，逻辑集中在 `api.py`。
- 线上：https://freelake.streamlit.app/
- 仓库：https://github.com/XiangQinxi/freelake
- 语言：全部注释、UI 文案、文档、commit message 均为**中文**；新增代码请保持一致。

### 当前实测环境

| 项 | 版本 / 值 |
| --- | --- |
| Python | 3.13.5 |
| streamlit | 1.61.1 |
| peewee | 4.3.0 |
| pillow | 12.3.0 |
| pandas | 3.0.5 |
| 测试状态 | `python -m pytest tests -q` → **25 passed**（约 10s） |

`requirements.txt` 用 `>=` 下限约束（不是 `==`），目的是兼容 Streamlit Cloud 的依赖解析。

### 启动 / 测试

```bash
pip install -r requirements.txt
streamlit run main.py            # 需要先有 .streamlit/secrets.toml（见 4.1）
python -m pytest tests -q        # 数据层单元测试（安全，不碰真实 data.db）；实测 25 passed ≈ 10s
```

> 受限沙箱环境注意：`tests/conftest.py` 会向系统临时目录 `tempfile.mkdtemp()` 写一份 `config.toml`。
> 若宿主对临时目录有 ACL 限制（例如只允许写工作区），会报
> `PermissionError: ... freelake_test_xxx\config.toml` —— 这是**环境问题，不是代码 bug**，
> 请在普通终端（或允许写临时目录的环境）里跑。

`.streamlit/secrets.toml` 当前**本地已存在且被 gitignore**，不要读取其内容、不要提交、不要打印。

---

## 2. 目录结构（真实状态）

```
freelake/
├── main.py                    # 入口：page_config + Cookie + 导航组装（196 行）
├── api.py                     # 数据层：peewee 模型 + 业务接口 + 导出/管理工具（1924 行，核心）
├── api2.py                    # 会话层：check_by_state()（25 行，刻意极薄）
├── const.py                   # 常量：tags(18 个标签)、admin="ADMIN"/user="USER"、announcement_tags
├── config.toml                # 仅 admin.userid/username，无密码（密码在 secrets/env）
├── requirements.txt
├── data.db                    # SQLite 运行库（gitignore，勿提交）
├── attachments/               # 附件（gitignore）——运行期生成
├── avatars/                   # 头像（gitignore）
├── static/
│   ├── background_720p.mp4    # 3.4MB
│   └── bg_embed.mp4           # 1.2MB，首页视频背景（home.py 实际用它）
├── default_avatar.jpeg        # 头像兜底图（在项目根，不在 avatars/）
├── pages/                     # st.navigation 页面（见 §5）
│   ├── home.py (909)          # 首页 / 详情 / 个人主页 三合一
│   ├── admin.py (579)         # 管理员后台
│   ├── image_generate.py (196)# AI 文生图（管理员）
│   ├── publish.py (148)       # 发布 + 草稿箱
│   ├── register.py (64) / login.py (44) / logout.py (21) / bookmarks.py (54)
│   └── logo.ico
├── tests/
│   ├── conftest.py (66)       # 关键：chdir 到临时目录后再 import api
│   └── test_data_layer.py (6xx)  # 32 个用例，覆盖数据层 + 安全边界
├── docs/
│   ├── model-refactor.md      # 数据模型 v1→v2 整理与重构报告（结构梳理/决策/变更/性能对照）
│   ├── model-usage-audit.md   # v1 模型外部依赖审计（字段×页面×测试的契约清单）
│   ├── project-review.md      # **项目审查总表**（P0×4 / P1×24 / P2×18 + 修复路线图）— 接手先读这个
│   ├── review-pages.md        # 分区审查：页面层（30 条）
│   ├── review-data.md         # 分区审查：数据层与测试（29 条 + 覆盖缺口）
│   └── review-ops.md          # 分区审查：工程化/配置/仓库卫生（23 条）
├── bench/                     # 性能基准（seed.py 造数 + bench_queries.py 测 20 条热路径）
│   ├── seed.py / bench_queries.py / _bootstrap.py
│   ├── baseline-current.json  # v1 基线 / after-refactor.json：v2 结果
│   └── README.md              # 用法、方法学、before/after 对照与已知回退
├── .streamlit/config.toml     # 主题（黑白极简、明暗双主题）+ server 配置
└── v1/                        # 上一代版本（含自己的 api.py/home.py…）——**不要改，也不要 import**
```

被 gitignore 且**不要纳入版本控制**：`.claude/`、`.agents/`、`.idea/`、`secrets.toml`、`__pycache__`、
`.pytest_cache/`、`attachments/`、`avatars/`、`data.db`、`demo.py`/`demo2.py`/`demo3.py`。
（`.agents/skills/developing-with-streamlit`、`.claude/skills/...` 目前是**空目录**，只是 skill 挂载点。）

---

## 3. 架构与调用链

```
浏览器
  ↓
main.py            # set_page_config、EncryptedCookieManager、st.session_state 初始化
  ↓                # 按登录态/角色组装导航（未登录只有 登录/注册）
st.navigation(nav).run()
  ↓
pages/*.py         # 每个页面脚本顶部直接执行（无函数入口）
  ↓
api2.check_by_state() ──→ api.User.check()  # 每次 rerun 都真实查库
  ↓
api.py             # 唯一数据访问入口；模块导入时完成建表+引导
  ↓
data.db (SQLite) + attachments/ + avatars/
```

**分层铁律**

1. 页面只调 `api.*` 与 `api2.check_by_state()`，**不直接写 SQL、不直接用 peewee 模型**（唯一例外：测试文件）。
2. 数据层不得 import 页面，也不应感知 UI（当前 `api.py` 里 import streamlit 只为了
   `st.secrets` / `st.cache_data`，属于历史妥协，新增逻辑尽量不要再依赖 st）。
3. `api2.py` 只做「会话状态 → bool」，不加业务。

---

## 4. 配置与凭据

### 4.1 secrets（`.streamlit/secrets.toml`）

```toml
[admin]
userid = "xqx"
username = "XiangQinxi"
password = "…"

[cookies]
password = "…"          # Cookie 加密密钥；更换后所有用户需重新登录

[ai]                    # 可选，仅「图片生成」页需要
base_url = "https://api.openai.com/v1"
api_key = "sk-…"
```

### 4.2 管理员凭据优先级

`st.secrets[admin|default_admin]` → 环境变量 `ADMIN_USERID/ADMIN_USERNAME/ADMIN_PASSWORD` → `config.toml`（兜底）。
实现见 `api.py:533 admin_credentials()`。`config.toml` **只放 userid/username，不放密码**。

### 4.3 Cookie 密钥优先级

`环境变量 COOKIES_PASSWORD` → `secrets[cookies].password` → **进程级随机密钥**（`secrets.token_urlsafe(32)` + `st.warning` 提示）。
硬编码兜底已删除（等同于把解密密钥写进仓库）。代价：未配置时服务重启/多进程部署后所有人都要重新登录，所以生产环境请配置。
见 `main.py:55-61`。

---

## 5. 页面职责与 URL 契约

| 页面 | 路由参数 | 职责 |
| --- | --- | --- |
| `pages/home.py` | 无 | 主页：搜索框、作者框、标签 pills、排序 `menu_button`、日期范围 `date_input`、 `st.pagination` 分页（page_size=5）、文章卡片列表 + 每篇最新一条评论 |
| `pages/home.py` | `?post_id=N` | 文章详情：正文（关键词高亮）、点赞/收藏、附件（密码门禁、图片预览、ZIP 打包下载）、评论区（楼层、Markdown、@提及徽章、编辑/删除/举报） |
| `pages/home.py` | `?user_id=X` | 个人主页：横幅 + 4 个指标（发布/浏览/获赞/获藏）+ **本人可见的账号设置**（改资料、改密码 popover、退出）+ 该用户文章列表 |
| `?search_keyword=` / `?filter_author=` | | 由主页搜索框写入 query_params，供详情页关键词高亮 |
| `pages/publish.py` | — | 编辑/预览双 Tab、标签 multiselect、多附件上传、附件专属密码、保存草稿、草稿箱继续编辑 |
| `pages/bookmarks.py` | — | 我的收藏（一次取 100 篇，不翻页） |
| `pages/login.py` / `register.py` / `logout.py` | — | 表单 + 写 Cookie 与 session_state |
| `pages/admin.py` | — | 见 §5.1 |
| `pages/image_generate.py` | — | OpenAI 兼容接口文生图/图生图，model 硬编码 `gpt-image-2`，结果存 `st.session_state.image_results` |

**导航装配（`main.py:166-180`）**：未登录 → 登录/注册；已登录 → 发布/收藏/(管理员: 管理后台 + 图片生成)/退出。
个人资料**不再是独立页面**（原 `pages/user_config.py` 已删除，commit `d2e7302`），合并到 `?user_id=本人`。

### 5.1 管理后台分区（`pages/admin.py`）

统计概览 → 数据图表（文章/评论/用户趋势、标签分布、作者 Top10、角色环形图）→ 用户管理（角色修改、带二次确认删除）→
文章管理 → 评论管理（全部/搜索/孤立评论清理）→ 举报管理（pending/handled/dismissed，可直接删目标）→
附件管理（孤立附件清理）→ 系统工具（**只读 SQL**、SHA256 工具、data.db 备份下载）→ 数据导出（用户/文章/评论 × CSV/JSON）→
文件管理（`st_file_browser` 限定 `attachments/`）。

表格渲染前统一剔除敏感列：`admin.py:153-155`（password、secret_key*）；只读 SQL 工具的结果也走
`api.mask_sensitive_rows()` 脱敏（password / secret_key* / attpassword → `***`）。

---

## 6. 数据模型 v2（`api.py` 模型区 + 适配层）

所有模型以 `_` 开头（peewee 实体），对外暴露无下划线业务类。时间仍是 `YYYY-MM-DD HH:MM:SS` **字符串**
（字典序 = 时间序，日期区间过滤直接字符串比较，**不要改成 datetime**）。

`SCHEMA_VERSION = 3`，落在 `PRAGMA user_version`。导入 `api.py` 时 `init_database()` 会：
1. 若发现 `_post` 表已存在且 `user_version != 2` → `db.close()` → 把 `data.db` 改名为
   `data.db.legacy-YYYYmmdd-HHMMSS` → 重连重建空库并打印提示（**不做数据迁移**，符合「可直接删库重建」的约定）；
2. `db.create_tables(ALL_MODELS, safe=True)`（`ALL_MODELS` 就是建表顺序，新增表必须加进去）；
3. 建两个 partial unique index（举报 pending 去重，`_create_partial_indexes`）；
4. 写入 `PRAGMA user_version = 2`。

连接参数：`SqliteDatabase("data.db", pragmas={"foreign_keys": 1, "busy_timeout": 5000})`。
**刻意不开 WAL**——`pages/admin.py` 的「下载 data.db 备份」直接读库文件字节，WAL 会漏掉未 checkpoint 的数据。

### 6.1 表结构

| 表 | 字段 | 说明 |
| --- | --- | --- |
| `_User` | userid(**PK**), username(idx), password(max 256), created_at, description, avatar, role(idx, CHECK ∈ {ADMIN,USER}), secret_key_hash(idx), secret_key_expires_at | **明文 secret_key 不再入库**：只存 `sha256(key)`，30 天过期（`SECRET_KEY_TTL_DAYS`），登录即轮换 |
| `_Post` | id(AutoField), author_id(idx, 逻辑外键), title, content, created_at, attpassword, views, like_count, bookmark_count, comment_count | **没有** attachments/tags/comments JSON 列；5 个 `(列, id)` 组合索引覆盖默认/作者/浏览量/点赞/收藏排序 |
| `_Tag` | id, name(**unique**) | 标签字典（`const.tags` 18 个 + 公告标签），发布时不存在的标签自动建 |
| `_PostTag` | post(FK CASCADE), tag(FK CASCADE), position；PK(post, tag) | 文章 ↔ 标签，`position` 保留发布时的选择顺序 |
| `_Attachment` | id, post(FK CASCADE, idx), original_name, stored_name(**unique**), mime, size, created_at | 附件元数据从 JSON 列升级为表 |
| `_Comment` | id, post(FK CASCADE, idx), user_id(逻辑外键, idx), content, created_at | 索引 `(post, created_at, id)` → 楼层次序即查询顺序 |
| `_Like` / `_Bookmark` | post(FK CASCADE), user_id, created_at；PK(post, user_id) | 主键天然去重；书签另有 `(user_id, created_at)` 索引 |
| `_Report` | id, post_id(idx), comment_id(idx, **NULL = 举报文章**), target_user_id, reporter_id, content_preview(≤500), reason, status(idx, CHECK ∈ {pending,handled,dismissed}), handled_by, handled_at, note(≤500), created_at | `post_id`/`comment_id` **故意不加外键**：举报是内容快照，要能活过内容删除 |
| `_Draft` | id, user_id(idx), title, content, tags(**仍是 JSON**), attpassword(**存明文**), created_at, updated_at | 草稿不被其它表引用，保持 JSON 最省事 |

外键：`PRAGMA foreign_keys=1` + `on_delete="CASCADE"` → `Post.delete()` 一条 DELETE 就清掉评论/点赞/收藏/
标签关联/附件（v1 只手工删评论，其余留孤儿）。作者、评论者、举报人**故意是逻辑外键**（CharField 无 FK），
这样删用户不会连带删内容，页面继续显示「已注销」。

### 6.2 冗余计数器

`_post.like_count/bookmark_count/comment_count` 是冗余列，`Like.toggle` / `Bookmark.toggle` /
`Post.add_comment` / `delete_comment` / `delete_comment_by_id` 都在**同一个 `db.atomic()`** 里改明细 + 改计数器。
因此 `sort_by="likes"/"bookmarks"` 直接走索引（v1 要全表 `GROUP BY` 再把全部文章拉进 Python 排序）。

批量造数据/改数据后请调 `Post.recount(postid)`，或一条 SQL 回填：

```sql
UPDATE _post SET like_count=(SELECT COUNT(*) FROM _like WHERE _like.post_id=_post.id),
                 bookmark_count=(SELECT COUNT(*) FROM _bookmark WHERE _bookmark.post_id=_post.id),
                 comment_count=(SELECT COUNT(*) FROM _comment WHERE _comment.post_id=_post.id);
```

`Post.get*` 返回的 dict 里 `like_count/bookmark_count/comment_count` 是**只读展示值**，别在页面里改。

### 6.3 适配层（页面零改动的关键）

模型列名是 snake_case，页面/测试读的仍是 v1 键名。所有转换集中在模型区下面的适配层：

- `_user_dict`、`_comment_dict(row)`（`postid`←`post`、`userid`←`user_id`）、`_report_dict`
  （`postid/commentid/target_userid/reporter_userid`）、`_draft_dict`、`_attachment_dict`
  （`saved_name`←`stored_name`、`type`←`mime`）
- `_tag_map` / `_attachment_map` / `_comments_map` / `_last_comment_map`：按 post_ids 批量取，各 1 条查询（防 N+1）
- `_post_dicts(rows, *, with_comments=False, with_last_comment=False)`：输出 v1 键
  `id/authorid/title/content/created_at/attachments/attpassword/tags/comments/views`

`comments` 的对外契约没变：`Post.get()`、`admin.py` 搜索文章拿到的 `comments` 是**评论 dict 的 JSON 字符串列表**
（按楼层次序、`ensure_ascii=False`），列表页函数返回 `comments: []`；需要「每篇最后一条评论」请用
`Post.get_last_comments(posts)`（现在直接读 `_comment` 表，不再依赖 `_Post.comments` 里的 ID 数组）。

**dicts() 的键是字段名**：`_Comment.post`（FK）在 `.dicts()` 里是 `"post"`，不是 `"post_id"`；但 peewee 实例上
仍是 `comment.post_id`。适配层统一用 `row["post"]`。

---

## 7. 数据层接口速查（`api.py`）

### User（实例方法 + 静态方法混用，注意 `user = User()`）

| 接口 | 说明 |
| --- | --- |
| `register(userid, username, password, role=user)` | 重复 userid 抛出唯一约束（`UNIQUE_VIOLATION`）时返回 False |
| `login(userid, password)` / `check = login` | PBKDF2 校验；旧 sha256 校验通过后**自动升级** |
| `check_admin(secret_key, *, admin_userid, admin_password)` | 先验账号密码，再退化为 secret_key 哈希比对 + 过期检查 |
| `exists / get_all / get_config / get_configs(iterable) / issue_secret_key / is_admin / count` | `get_configs` 单次查询批量取，列表页必须用它防 N+1；`issue_secret_key(userid, password)` 签发 30 天密钥（明文只返回一次） |
| `modify(userid, password, username=, description=, avatar=, new_password=, admin_secret_key=)` | 需原密码或管理员密钥；**`role` 形参已删除**（改角色走 `set_role`），`avatar` 必须是白名单文件名 |
| `set_role(admin_secret_key, target_userid, role, *, admin_userid=, admin_password=)` | 改角色唯一入口；`secret_key` 或「会话里的管理员自己」二者之一通过即放行 |
| `delete(userid, password, *, admin_secret_key=, admin_userid=, admin_password=)` | 同上（不级联删其文章，作者列是逻辑外键） |

### Post（全静态方法）

| 接口 | 说明 |
| --- | --- |
| `publish(authorid, title, content, attachments, attpassword=None, tags=None)` | 单事务：文章 + 附件 + 标签；附件密码在此处 sha256；返回自增 id |
| `delete(postid)` / `edit(postid, title, content, tags)` | 删除靠外键级联；编辑会整体替换标签关联 |
| `_apply_filters(query, keyword, tag, post_ids, author, start_date, end_date)` | 唯一过滤真相源，计数与分页共用；`tag` 走 `_tag`/`_post_tag` 子查询 |
| `count_filtered(...)` / `get_filtered_paginate(..., page, page_size, sort_by)` | `sort_by ∈ {date, views, likes, bookmarks}`，**四种排序全部 SQL 下推** |
| `get_by_author(authorid)` / `get_author_stats(authorid)` | 个人主页；统计是单条聚合（`COUNT` + `SUM(views/like_count/bookmark_count)`） |
| `add_view(postid)` | 单条 `UPDATE views = views + 1` |
| `get(_id)` | 详情；`_post_dicts(..., with_comments=True)` |
| `get_last_comments(posts)` | 一次查询取每篇最后一条评论 → `{post_id: comment}` |
| `add_comment / edit_comment(postid, comment_index, content) / delete_comment(postid, comment_index)` | 对外仍**按楼层次序下标**定位；内部 `_comment_id_at` 用 `order_by(created_at, id).offset(index)` 换算成真实 id |
| `recount(postid)` | 重算三个计数器，返回 `{"likes": n, "bookmarks": n, "comments": n}`（批量修数据后用） |

### 其它

- `Attachment.save(uploaded_file) -> dict{original_name, saved_name(uuid+ext), type, size, path}`（元数据落 `_attachment` 由 `Post.publish` 完成）；
  `get_file(saved_name)` 有 `os.path.basename` 防穿越；`get_thumbnail_bytes(saved_name, max_width=300)`
  带 `@st.cache_data(ttl="10m", max_entries=256)`；`get_thumbnail_base64(...)`。
- `Like / Bookmark`：`toggle(postid, userid) -> bool(切换后状态)`、`is_liked/is_bookmarked`、`count`（读 `_post` 计数器）、
  `Bookmark.get_bookmarked_post_ids(userid)`。
- `Report.add(reporter_userid, *, postid, commentid, target_userid, content_preview, reason)`：靠两个 partial unique
  index 做「同一人 + 同一目标 + pending」去重（冲突时 `except UNIQUE_VIOLATION` 返回 None）；举报文章与举报其评论
  是不同目标。`handle(report_id, handled_by, action, note)`，action 仅 `handled|dismissed`。
- `Draft.save(userid, title, content, tags, attpassword, draft_id=None)` / `get_drafts` / `get_draft` / `delete` / `count`（全部限制本人）。
- `save_avatar(uploaded_file)`：中心 1:1 裁切 → 缩到 `MAX_AVATAR_DIM=512` → 统一 RGB JPEG `quality=85` → 存 `avatars/<uuid>.jpg`；
  `get_avatar_bytes(name)` 兼容旧绝对路径、`default_avatar.jpeg` 走项目根。
- 导出：`export_users_csv/json`、`export_posts_csv/json`、`export_comments_csv/json`，CSV 均带 `utf-8-sig` BOM。
  **v2 起 JSON 导出脱敏**：`export_users_json` 不再含 `password`/`secret_key`，`export_posts_json` 不再含 `attpassword`
  （CSV 本来就只有有限列）。
- 管理员工具：`get_attachment_stats`（单条聚合）、`get_all_attachments`（join `_Post.title`，多返回 `post_id/post_title`）、
  `get_orphaned_attachments` / `delete_orphaned_attachments`、`get_comment_summary`（含 `comment_index`，按楼层次序）、
  `delete_comment_by_id`、`search_comments`、`get_orphaned_comments` / `delete_orphaned_comments`（外键级联后恒空，保留兼容）、
  `format_size`。
- `execute_sql(query)`：**双层只读守卫**——① 首词必须是 `SELECT / WITH / EXPLAIN / PRAGMA`，`PRAGMA` 不允许 `=` 赋值；
  ② 执行期间挂 SQLite `set_authorizer`，写入动作（含 `WITH … DELETE`、写型 PRAGMA）在编译期被 `SQLITE_DENY`。
  结果再经 `mask_sensitive_rows()` 脱敏。
---

## 8. 关键约定与坑（改代码前必读）

### 8.1 `api.py` 导入即有副作用 —— 最重要

**import 时**依次执行：`init_database()`（旧库自动改名备份 → `create_tables(ALL_MODELS)` → partial index →
`PRAGMA user_version = 3`）、按凭据创建管理员、`bootstrap_announcements()`。
因此：
- `data.db` 与 `config.toml` 都是**相对当前工作目录**的路径（`SqliteDatabase("data.db")`、`open("config.toml")`）。
  在别的 cwd 启动会得到一套空库。→ **任何脚本/测试都必须先 chdir 到受控目录再 import api**（`tests/conftest.py:26-30` 就是这么做的）。
- 不要在模块顶层再做别的一次性写操作。
- 公告幂等：以「管理员 + 定稿标题 `ANNOUNCEMENT_POST_TITLE`」判定，管理员删掉后下次启动会重发。
- **旧库会被自动换掉**：`_post` 已存在但 `user_version != 2` 时，`data.db` 被改名成 `data.db.legacy-YYYYmmdd-HHMMSS`，
  随后重建空库。所以「改模型 → 重启」就能拿到新结构，**不需要手写 ALTER**；留下的 `.legacy-*` 可手工删。

### 8.2 Streamlit 特有陷阱

| 陷阱 | 现状 / 正确做法 |
| --- | --- |
| `streamlit_cookies_manager` 仍调用已被删除的 `st.cache` | `main.py:26-27` 补 `st.cache = st.cache_data` 别名，**不要删** |
| Cookie 首轮未就绪 | `main.py:67-72`：**不要 `st.stop()`**，否则服务端导航后死锁在「正在加载 Cookies」 |
| 服务端导航 vs 客户端导航 | 页面内跳转优先 `st.page_link`（客户端），`st.switch_page` 会触发 Cookie 组件重载 —— `publish.py:132` 仍在用 switch_page（发布后跳首页），改这块要实测 |
| `st.set_page_config` | 只能出现在 `main.py:44`；页面里再调会报错（`image_generate.py` 注释已说明） |
| `check_by_state()` 不能缓存 | 曾因记忆化把正确凭据缓存成 False 导致登录失败（`api2.py:11-12`） |
| 深色主题 primary 白底白字 | `main.py:81-122` 注入 CSS 把按钮/标签/chips 文字改黑，选择器依赖 `data-testid` |
| 移动端适配 | `main.py:125-149` 媒体查询收紧留白、栅格允许换行 |
| 浏览量重复计数 | `home.py:507-509` 用 `state["viewed_<post_id>"]` 按会话去重 |
| 首页默认日期范围 | `home.py:41 DEFAULT_DATE_START = 2026-08-01`——比它早的文章**默认不显示**，排查「文章不见了」先看这里 |

### 8.3 数据层约定

- 标签/附件/评论**已无 JSON 列**：标签走 `_tag` + `_post_tag`（`_sync_tags`），附件走 `_attachment`（`_sync_attachments`），
  评论走 `_comment`。只有 `_draft.tags` 仍是 `playhouse.mysql_ext.JSONField`（顶部那句 import 仅为它保留，带 `# NOQA`）。
- 标签过滤走 `_tag` 子查询（`_PostTag...where(_Tag.name == tag)`），**不要**用 `contains` 模糊匹配。
- 时间一律 `now_str()`（`YYYY-MM-DD HH:MM:SS`），不要引入 datetime 对象入库。
- 写明细表必须同步计数器：`Like/Bookmark.toggle`、`Post.add_comment/delete_comment/delete_comment_by_id` 都在
  `db.atomic()` 里同时改 `_post.*_count`；新增「删除/恢复」类逻辑时别忘了，否则排序与计数会漂（补救用 `Post.recount`）。
- 唯一/外键冲突捕获统一用 `UNIQUE_VIOLATION = (peewee.IntegrityError, sqlite3.IntegrityError)`：
  `peewee.IntegrityError` **不是** `sqlite3.IntegrityError` 的子类。
- `Model.select().dicts()` 的键是**字段名**：FK `_Comment.post` 读 `row["post"]`（实例上仍是 `comment.post_id`）。
- `Check(...)` 的签名是 `Check(constraint, name=None)`：`Check("role IN ('ADMIN','USER')")`，第二个位置参数是**约束名**。
- 文件只存**文件名**（相对路径），不存绝对路径，便于迁移；读取前一律 `os.path.basename` 防目录穿越。
- 用户密码：新格式 `pbkdf2$200000$<salt>$<digest>`（`PBKDF2_ITERATIONS = 200_000`）；
  旧 `sha256(明文+"freelake")` 仅用于兼容与附件密码。
- `role` 字段值比较必须用 `const.admin` / `const.user`，不要写裸字符串 `"ADMIN"`（v2 另有 CHECK 约束只允许 ADMIN/USER）。
- 导出 CSV 必须 `utf-8-sig`；JSON 必须 `ensure_ascii=False`（评论 JSON 同样，`_comments_map` 已带）。
- JSON 导出已脱敏：`export_users_json` 去 `password`/`secret_key`，`export_posts_json` 去 `attpassword`；加导出列先判是否敏感。
- 列表页禁止 N+1：作者配置用 `User.get_configs`，最新评论用 `Post.get_last_comments`，附件文件**一个文件只读一次**（`home.py:543-554`）。

### 8.4 安全红线

- 不要把 `password` / `secret_key` 输出到 UI、日志、导出（管理页已剔除，SQL 结果走 `mask_sensitive_rows`）。
- 不要把 secrets、`data.db`、附件、头像提交进 git。
- `execute_sql` 的守卫是**两层**（首词白名单 + SQLite authorizer），任何一层都不要放宽。
- 文件浏览器只能指向 `attachments/`，不能指向项目根。
- 页面级鉴权必须写在**页面里**（`pages/admin.py`、`pages/image_generate.py` 顶部的 `User().is_admin`），
  导航里隐藏入口只是 UI 便利，不是鉴权——Streamlit 直接访问 URL 时会单独执行页面脚本。
- 上传：`Attachment.save` 限制 20MB（`MAX_ATTACHMENT_BYTES`）且扩展名过白名单；`maxUploadSize = 20` 是第二道闸。

### 8.5 性能约定（v3 起）

- 密码校验成功结果在会话内缓存 5 分钟（`_PASSWORD_OK_TTL`），缓存键含库里的密码哈希 → 改密自动失效。
  不要在缓存里存 False：曾出现「正确密码被缓存成 False 导致登录失败」。
- 附件字节按文件名缓存（`_read_attachment_file`，`st.cache_data` 30 分钟）：附件名是 uuid、内容不可变，
  多图帖子的列表页/详情页因此不再每次 rerun 重读磁盘。
- ZIP 打包走 `build_attachments_zip(post_id, entries)`（缓存 30 分钟），不要在页面里手写 `ZipFile`。
- 管理后台的「统计概览 + 数据图表」共用 `_admin_snapshot(version)`（缓存 2 分钟）；
  任何写操作之后必须调用 `_refresh()`（内部先 `_bump_admin_data()` 再 `st.rerun()`），否则看到的还是旧快照。
- 列表页禁止 N+1，也别做「读了却不用」的 I/O：`pages/home.py` 曾有整帖附件读盘后丢弃的死代码，已删。

---

## 9. 测试

```bash
python -m pytest tests -q          # 32 个用例，实测全绿（≈11s）
```

`tests/conftest.py` 的设计（**不要改坏**）：
1. 把项目根加入 `sys.path`；
2. `tempfile.mkdtemp()` 后 `os.chdir`，并写一份最小 `config.toml`（`testadmin` 无密码）——
   保证 `data.db` / `config.toml` 都落在临时目录，**不污染真实数据**；atexit 清理。
3. `_clean_db`（autouse）：每个用例结束**逆序**清空 `api.ALL_MODELS`（子表先删，外键才不会拦）。新增表必须进 `ALL_MODELS`。
4. `tmp_dirs` fixture：monkeypatch `api.ATTACHMENTS_DIR` / `api.AVATARS_DIR` 到 tmp_path。
5. `FakeUpload`：模拟 `UploadedFile`（`name/type/size/getvalue()`）。

已覆盖：注册/重复注册/登录/**旧 sha256 自动升级**/hash-verify 往返/**改密需原密码**（B1 回归）、
文章自增 ID（B5）、编辑删除、评论增删改 + 时间格式（B6/B3）、点赞收藏 toggle、浏览量（F6）、
组合筛选与计数口径一致（B2/F4）、日期区间、四种排序、作者列表与统计（F3/B4/B5）、
附件保存 + **目录穿越拦截**（S2）、缩略图、头像压缩、举报去重 + 状态流转 + 文章/评论目标区分、
草稿 CRUD + 越权拦截、**只读 SQL 守卫**（S3）。

**未覆盖**：所有 UI 行为（`pages/*`）、登录 Cookie 生命周期、图片生成页、`execute_sql` 之外的管理工具、
并发（SQLite 单写入锁）。改 UI 只能靠手动跑 `streamlit run main.py` 验证。

注：`test_data_layer.py` 文首注释写「内存库」，实际是**临时文件库**，以 conftest 为准。

---

## 10. 已知问题 / 待决事项

> **完整清单见 `docs/project-review.md`**（4 条 P0：只读 SQL 可绕、Cookie 密钥 fail-open、50MB 幽灵 blob、playhouse 依赖幻觉包；24 条 P1；18 条 P2）。
> 下面只列与架构强相关、需要改代码才能消化的历史条目。

1. **仓库里有一个约 49.7MB（52,154,578 字节）的 `background.mp4` 处于「已暂存但工作区已删除」状态**（`git status`: `new file` + `deleted`，未提交）。
   它不在 `requirements`/代码引用里（代码用的是 `static/bg_embed.mp4` 与 `static/background_720p.mp4`）。建议先与维护者确认再 `git restore --staged` 或提交。
2. `pages/admin.py` 顶层 `import altair`，但 `altair` 未写入 `requirements.txt`（靠 streamlit 自带依赖传递）。若要显式声明需先确认版本。
3. `requirements.txt` 的版本号（streamlit≥1.61、peewee≥4.3、pandas≥3.0、Pillow≥12.3）是「未来版本」番号，与当前实测环境一致但要留意云端解析差异。
4. `publish.py:132` 用 `st.switch_page` 跳首页，而 `bookmarks.py:5-9` 注释明确说应避免 `switch_page`（Cookie 重载风险）。二者行为不一致，属潜在隐患。
5. 草稿**不保存附件**（只存标题/内容/标签/附件密码），发布前需重新上传（设计选择，避免孤立文件）。
6. 缩略图缓存 10 分钟：替换同名附件后首页最多延迟 10 分钟刷新（README 已记录）。
7. `data.db` 单文件 SQLite。v2 起有 `SCHEMA_VERSION`/`PRAGMA user_version`：结构版本不符时**自动把旧库改名成
   `data.db.legacy-<时间戳>` 再重建空库**，所以改表结构不需要手写迁移；代价是**不做数据迁移**（历史数据只在 `.legacy` 文件里）。
   若要真正搬迁数据，需自己写脚本读 `.legacy` 库并按新结构写入。
8. `v1/` 是上代完整副本，仍是 import 风险源（有同名 `api.py`/`main.py`），**不要把它加入 sys.path**。

---

## 11. 给下一个 Agent 的工作规则

1. **先读后改**：动 `api.py` 前先看 §7 对应接口与 §6 表结构；动页面先看 §5 路由契约。
2. **改数据层必须跑** `python -m pytest tests -q`；改完仍要全绿。
3. **改 UI 必须实测**：`streamlit run main.py`（需要 secrets）；重点回归登录→发布→详情→评论→收藏→管理后台这条主链路。
4. **风格**：保持中文注释与文档字符串；函数写类型提示（`str | None`、`list[dict[str, str]]`）；保留既有 `# NOQA` 与分区注释 `# region ... # endregion`。
5. **格式化**：项目用 ruff（存在 `.ruff_cache`），提交前 `ruff format .` + `ruff check .`（若无配置则保持与四周代码一致）。
6. **不要动**：`v1/`、`.streamlit/secrets.toml`、`attachments/`、`avatars/`、`data.db`、`default_avatar.jpeg`、`pages/logo.ico`、`static/*.mp4`。
7. **不要新增依赖**，除非确实必要并同步更新 `requirements.txt` 与 README。
8. **提交信息**中文，与历史风格一致（`A2 · 依赖清理与锁定`、`① 移除 pages/user_config.py…`）。
9. 改动涉及安全（密码、SQL、文件路径、权限判定）时，在回复中显式说明影响面。

---

## 12. 5 分钟上手清单

```text
[ ] streamlit run main.py 能起来，首页有视频背景与文章列表
[ ] 能注册/登录（注意 Cookie 首次「正在加载 Cookies」一闪而过是正常的）
[ ] 发布一篇文章（带 1 张图片附件），详情页能看缩略图 + ZIP 下载
[ ] 评论 + 点赞 + 收藏 → 「我的收藏」里能看到
[ ] 管理员账号能看到「管理员页面」与「图片生成」入口
[ ] python -m pytest tests -q 全绿
```

掌握以上 6 条即可安全开始改动。有疑问优先看 `README.md`（面向用户/部署）与本文（面向开发/AI）。

