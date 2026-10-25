# FreeLake 模型调用契约审计（重构前基线）

- 审计时间：2026-10-25 13:02:06 +08:00
- git HEAD：`37f9f48`（"提供分辨率"，分支 `master`）
- 工作区状态：`?? AGENT.md`、`AD background.mp4`（已暂存但工作区删除）——与本审计无关
- 审计范围：`api.py`(1454 行)、`api2.py`(25)、`const.py`(35)、`main.py`(196)、`pages/{home,admin,publish,bookmarks,register,login,logout,image_generate}.py`、`tests/{conftest,test_data_layer}.py`
- 排除：`v1/`（上代副本，禁止 import）、`api2.py` 之外的其它旧文件
- 方法：**纯静态阅读**。未运行任何会 `import api` 的脚本、未跑 pytest、未读写 `data.db`、未修改除本文件外的任何文件。
- 口径：表结构以 `api.py:97-188` 的 `Field` 声明为准；行号即审计时文件行号。
- 规模：**7 张表**（`_User/_Post/_Comment/_Like/_Bookmark/_Report/_Draft`）、**47 个显式 Field** + 3 个隐式 `id` 自增主键（`_User`/`_Like`/`_Bookmark` 未显式声明主键）= **50 列**。
- 图例：`[W]`=写、`[R]`=读/查询条件、`[UI]`=UI 直接渲染/消费、`[EXP]`=进导出、`[FK]`=逻辑主键/外键、`[EXPR]`=peewee 表达式或原生 SQL。

---

## 0. 数据模型总览

| 模型类 | 物理表名（peewee 默认小写） | 显式字段数 | 主键 | 备注 |
| --- | --- | --- | --- | --- |
| `_User` | `_user` | 8 | 隐式 `id` AutoField | 业务主键是 `userid`(CharField，**无 unique / 无索引**) |
| `_Post` | `_post` | 10 | `id` AutoField（显式） | `attachments/tags/comments` 为 `JSONField`（`playhouse.mysql_ext`） |
| `_Comment` | `_comment` | 5 | `id` AutoField（显式） | `postid` 无条件外键，仅 IntegerField |
| `_Like` | `_like` | 2 | 隐式 `id` | **无唯一约束**，靠 `get_or_none` 幂等 |
| `_Bookmark` | `_bookmark` | 2 | 隐式 `id` | 同 `_Like` |
| `_Report` | `_report` | 12 | `id` AutoField（显式） | `postid`/`commentid` 均可空，靠 null 区分举报对象 |
| `_Draft` | `_draft` | 8 | `id` AutoField（显式） | `attpassword` 存**明文**（与 `_Post.attpassword` 哈希不一致） |

建表/连接：`api.py:41 db = SqliteDatabase("data.db")`、`api.py:278 db.connect()`、`api.py:279 db.create_tables([...], safe=True)`（`safe=True` 不会 ALTER 既有表）。
引导副作用：`api.py:282-292`（管理员账号）、`api.py:1451 bootstrap_announcements()`。
表名是原始 SQL 可见契约：`admin.py:471` 的占位示例写死 `SELECT * FROM _user`；`tests/test_data_layer.py:570` 用 `from _user`。

---

## 1. 字段级读写点

### 1.1 `_User`（`api.py:102-116`）

#### `userid` — CharField，业务主键
- `[W]` `api.py:307` `_User.create(userid=userid,`)；`api.py:285-291` 引导创建管理员；`api.py:450 user.delete_instance()`
- `[R]` `api.py:320` `_User.get_or_none(_User.userid == userid)`（登录）；`api.py:350` `exists()`；`api.py:360` `get_config()`；`api.py:387` `_User.userid.in_(ids)`；`api.py:393` `get_secret_key()`；`api.py:415` `modify()`；`api.py:447` `delete()`
- `[R]` `api.py:539` `_User.userid.contains(author)`（作者模糊搜索 → `author_ids` → `_Post.authorid.in_(...)`）
- `[EXP]` `api.py:1171/1174` 用户 CSV 第 1 列；`api.py:1183` 用户 JSON 全列
- `[R]` `api.py:1395` `_User.get_or_none(_User.userid == admin_id)`（公告引导）
- `[UI]` `home.py:92` `user.get_config(state["userid"])`；`home.py:379` `user.get_config(user_id)`；`home.py:736` `query_params={"user_id": state.get("userid","")}`；`home.py:665` `avatar_link(..., comment["userid"], 40)`
- `[UI]` `admin.py:151` `pd.DataFrame(user.get_all())` 直接展示 `userid` 列；`admin.py:164` `user.get_config(search_uid)` → `st.json(cfg)`
- `[UI]` `register.py:52-60` `cookies/state["userid"]`；`login.py:33-40`；`api2.py:25` `state.get("userid","")`
- `[FK]` 被 `_Post.authorid`、`_Comment.userid`、`_Like.userid`、`_Bookmark.userid`、`_Report.target_userid/reporter_userid`、`_Draft.userid` 以**裸 CharField** 引用；无 `ForeignKeyField`，删除用户不级联

#### `username` — CharField
- `[W]` `api.py:307` `register`；`api.py:419` `user.username = username`（`modify`）
- `[R]` `api.py:364/381` `get_config/get_configs` 返回；`api.py:540` `_User.username.contains(author)`
- `[EXP]` `api.py:1171/1174` CSV 第 2 列；JSON 全列
- `[UI]` `home.py:234` `username = _config["username"]`；`home.py:384` `st.markdown(f"### {userconfig.get('username')}")`；`home.py:417` `value=userconfig.get("username")`；`home.py:432` `username=new_username`；`home.py:667` `**{cfg['username']}**`；`home.py:884` `last_user["username"]`
- `[UI]` `bookmarks.py:41` `cfg['username']`；`admin.py:151` DataFrame 列

#### `password` — CharField(max_length=256)，PBKDF2 或旧 sha256
- `[W]` `api.py:309` `password=hash_password(password)`；`api.py:288` 管理员引导；`api.py:324-325` 旧 sha256 登录成功后自动升级；`api.py:427` `user.password = hash_password(new_password)`
- `[R]` `api.py:321` `verify_password(password, user.password)`（唯一读取点）
- `[EXP]` `api.py:1183-1185` `export_users_json` 用 `.dicts()` **原样导出全部列 → 含 password 哈希**（CSV 不含）
- `[UI]` 无页面直接读 DB 密码；`admin.py:153-155` 表格渲染前剔除 `password`/`secret_key`
- 格式契约：`pbkdf2$200000$<salt>$<digest>`（`api.py:255-261`），旧格式 `sha256(明文+"freelake")`（`api.py:247-249`）；`test_data_layer.py:68,74` 断言 `startswith("pbkdf2$")`

#### `created_at` — CharField，`YYYY-MM-DD HH:MM:SS` 字符串
- `[W]` `api.py:290`（管理员引导）、`api.py:311`（注册）；**无更新路径**
- `[R]` `api.py:366/383` 配置返回；`api.py:1171/1174` CSV 第 3 列
- `[UI]` `home.py:388` `注册于 {userconfig.get('created_at')}`；`admin.py:102` `_daily_counts(users_all)` → 注册趋势图；`admin.py:62` `r[date_key][:10]`（**依赖字符串前 10 位是日期**）
- `[FK]` 无

#### `description` — TextField(default="这个用户有点懒，什么也没留下~")
- `[W]` `api.py:420-421` `user.description = description`（仅 `modify`）
- `[R]` `api.py:367/384` 配置返回；`api.py:1171/1174` CSV 第 4 列
- `[UI]` `home.py:390` `st.markdown(userconfig.get("description") or "这个用户很懒，什么也没留下~")`（注意兜底文案与默认值**不一致**）；`home.py:419-423` `value=userconfig.get("description")`；`home.py:433` `description=new_description`

#### `avatar` — CharField(default="default_avatar.jpeg")，存**文件名**
- `[W]` `api.py:422-423` `user.avatar = avatar`；`home.py:434` 传入 `meta.get("path")`（来自 `save_avatar`）
- `[R]` `api.py:368/385` 配置返回；`api.py:1142-1157` `get_avatar_bytes` 读取（兼容旧绝对路径；`default_avatar.jpeg` 走项目根 `DIR`；否则 `os.path.basename` 后拼 `AVATARS_DIR`）
- `[UI]` `home.py:233` `avatar = _config["avatar"]`；`home.py:239` `avatar_link(col_1, avatar, post["authorid"], 55)`；`home.py:383` `col_h.image(get_avatar_bytes(userconfig["avatar"]), width=150)`；`home.py:665` `cfg["avatar"]`；`home.py:73-74` `get_avatar_bytes(avatar_name or "default_avatar.jpeg")`
- 契约：**只存文件名/相对名**，不存绝对路径（`api.py:15-16` 注释、`api.py:1152`）

#### `role` — CharField(default="user")
- `[W]` `api.py:310` `role=role`（默认 `const.user="USER"`，`api.py:302`）；`api.py:289` 管理员 `role=admin`；`api.py:424-425` `user.role = role`（`modify`）；`admin.py:179-184` 改角色，值为硬编码 `"USER"/"ADMIN"`（`admin.py:173`）
- `[R]` `api.py:340` `self.get_config(admin_userid)["role"] == admin`；`api.py:344` `user.role == admin`；`api.py:367/384` 配置返回；`api.py:1171/1174` CSV 第 5 列 / JSON 全列
- `[UI]` `home.py:244` `userconfig["role"] == admin`；`home.py:319` `userconfig.get("role") == admin`；`home.py:386` `role_name = "管理员" if ... == admin`；`home.py:530` `userconfig["role"] != admin`；`home.py:676`；`home.py:739` `current_cfg.get("role") == admin`
- `[UI]` `main.py:177` `userconfig.get("role") == admin`（导航可见性）；`admin.py:132` 角色环形图 `[u["role"] for u in users_all]`
- **约定**：判定必须 `== const.admin("ADMIN")`。历史数据存在小写 `"user"`（默认值），不可写 `!= const.user`。`const.py:31-32`

#### `secret_key` — CharField(max_length=100, default=lambda: generate_sk_key(16))
- `[W]` 创建默认值；`api.py:236-244 fix_duplicate_secret_keys()` 修复历史重复并 `u.save()`
- `[R]` `api.py:342` `_User.get_or_none(_User.secret_key == secret_key)`（`check_admin` 第二路径）；`api.py:395` `get_secret_key()` 返回
- `[EXP]` `api.py:1183-1185` 用户 JSON 全列导出 → **含 secret_key**
- `[UI]` `register.py:53/57/60`、`login.py:33/37/40` 写入 `cookies["secretkey"]` / `state["secretkey"]`；`admin.py:183/210` `admin_secret_key=state["secretkey"]` 用于改角色/删用户
- 安全契约：`check_admin` 里 secret_key 等价 ADMIN 凭据（`api.py:331-345`）

---

### 1.2 `_Post`（`api.py:119-131`）

#### `id` — AutoField（显式，`api.py:122`）
- `[W]` 数据库自增；`api.py:472-481` `publish` 返回 `post.id`
- `[R]` `api.py:486/489` delete；`api.py:496` edit；`api.py:534` `_Post.id.in_(post_ids)`；`api.py:546` `_Post.id == -1`；`api.py:606/620` 排序 `_Post.id.desc()`；`api.py:615` `p["id"]`；`api.py:631` `order_by(_Post.id.desc())`；`api.py:639`；`api.py:655` update where；`api.py:664` `Post.get`；`api.py:1397-1399` 公告幂等判定（`authorid` + `title`，非 id）
- `[FK]` `_Comment.postid`、`_Like.postid`、`_Bookmark.postid`、`_Report.postid` 均指向它（无约束）；`_Post.comments` 数组元素指向 `_Comment.id`
- `[EXP]` `api.py:1211` CSV 第 1 列
- `[UI]` `home.py:299` `"文章ID": post["id"]`；`home.py:493/874` `params["post_id"] = str(p["id"])`；`home.py:580/622` download key；`admin.py:244-254` `st.json({"id": p["id"], ...})`
- 关键：自增不允许重排/改成 UUID（`test_data_layer.py:109-118` B5 回归 + `home.py:493` URL 参数）

#### `authorid` — CharField
- `[W]` `api.py:473` `publish`；`api.py:1403` 公告发布。`edit()` **不改** authorid（`api.py:493-503`）
- `[R]` `api.py:535-543` 作者过滤；`api.py:630` `get_by_author` 条件；`api.py:638` `get_author_stats` 条件；`api.py:1212` CSV 第 2 列
- `[UI]` `home.py:183` `target_userid=post["authorid"]`（举报）；`home.py:231` `user.get_config(post["authorid"])`；`home.py:239`；`home.py:244/247` 与当前用户比较；`home.py:318` `state["userid"] == post.get("authorid")`；`home.py:529`；`home.py:578` ZIP 文件名；`home.py:858` `author_configs.get(post["authorid"])`
- `[UI]` `bookmarks.py:32` `User.get_configs(p["authorid"] for p in posts)`；`bookmarks.py:36`
- `[UI]` `admin.py:122` `authors = [p["authorid"] for p in posts_all]` → 作者 Top10 图
- `[FK]` → `_User.userid`（逻辑）

#### `title` — TextField
- `[W]` `api.py:474` publish；`api.py:498` `post.title = title`（edit）；`api.py:1404` 公告
- `[R]` `api.py:529` `_Post.title.contains(keyword)`；`api.py:1398` 公告幂等 `_Post.title == ANNOUNCEMENT_POST_TITLE`；`api.py:1213` CSV 第 3 列
- `[UI]` `home.py:127` `value=post["title"]`（编辑弹窗）；`home.py:240` `col_2.subheader(post["title"])`；`home.py:173` 举报弹窗 `f"举报文章：**{post['title']}**"`；`home.py:184` `content_preview=(post["title"] or "")[:200]`；`home.py:578` ZIP 文件名
- `[UI]` `bookmarks.py:37` `st.markdown(f"### {post['title']}")`；`admin.py:247` `"title": p["title"]`；`api.py:1279` `post_title`（附件列表）

#### `content` — TextField
- `[W]` `api.py:476` publish；`api.py:499` `post.content = content`（edit）
- `[R]` `api.py:530` `_Post.content.contains(keyword)`；`api.py:1214` CSV 第 4 列
- `[UI]` `home.py:133` `value=post["content"]`；`home.py:516` `st.markdown(highlight_keyword(post["content"], ...))`；`home.py:483/860-862` 列表摘要 `[:40]/[:150]`；`bookmarks.py:45-49` `[:60]`
- `[UI]` `admin.py:247-248` `"content": p["content"][:200] + "..."`

#### `created_at` — CharField
- `[W]` `api.py:475` publish；公告经 publish
- `[R]` `api.py:549` `_Post.created_at >= start + " 00:00:00"`；`api.py:553` `<= end + " 23:59:59"`；`api.py:620` `order_by(_Post.created_at.desc(), ...)`；`api.py:1215` CSV 第 5 列
- `[UI]` `home.py:290` `{post['created_at']}`（紧凑摘要）；`home.py:297` 元信息表格"发布时间"；`bookmarks.py:42`
- `[UI]` `admin.py:84` `_daily_counts(posts_all)`（默认 `date_key="created_at"`）→ 文章趋势；`admin.py:62` `[:10]`
- 契约：**字符串字典序=时间序**；不得改 datetime 对象/Unix 时间戳

#### `attachments` — JSONField(default=list)，元素为 `Attachment.save()` 的 dict
- 元素键固定：`original_name` / `saved_name` / `type` / `size` / `path`（`api.py:796-802`）
- `[W]` `api.py:477` publish 写入；`edit()` **不改**；`_Draft` 不存附件
- `[R]` `api.py:1216-1217` CSV `len(p.get("attachments"))` → `attachments_count`；`api.py:1268-1270` `get_attachment_stats` `att.get("size",0)`；`api.py:1278-1279` `get_all_attachments` `{**att, "post_id":..., "post_title":...}`；`api.py:1287-1289` `get_orphaned_attachments` `att.get("saved_name")`
- `[UI]` `home.py:521` `post.get("attachments", [])`；`home.py:546-554` `att.get("saved_name", "")` → `Attachment.get_file`；`home.py:560` `original_name`；`home.py:562/564` `att.get("type")`/`att['type']`；`home.py:572` `original_name` 作 ZIP 内名；`home.py:593-594` `original_name`/`size`；`home.py:606/610/614` `att['type']` 分派 image/video/audio；`home.py:619-621` 下载
- `[UI]` `home.py:890-908` 列表页同款消费；`admin.py:251` `len(p.get("attachments", []))`；`admin.py:439-443` `pd.DataFrame(get_all_attachments())` 展示 `saved_name/type/size/path/post_id/post_title`
- `[FK]` `saved_name` → `attachments/` 目录文件名（`api.py:791-793, 846-855`）

#### `attpassword` — CharField(max_length=256, default="")，存 `sha256(明文)`
- `[W]` `api.py:468-471` `if attpassword: attpassword = sha256(attpassword) else ""`（仅 publish）
- `[R]`（仅 UI 读）`home.py:525` `attpassword = post.get("attpassword")`；`home.py:894`
- `[UI]` `home.py:527-541` 密码门禁：`state["attpassword"] != attpassword` → 输入 → `sha256(attpwd) == attpassword`；`state["attpassword"]` 在 `home.py:104` 初始化为 `sha256("")`
- `[EXP]` `api.py:1228` posts JSON 全列导出 → 含该哈希
- 契约：sha256(salt="freelake")，UI 直接比较字符串；**不能换成 PBKDF2/随机盐**，否则 `home.py:538-539` 失效

#### `tags` — JSONField(default=list)，元素为 `str`
- `[W]` `api.py:479` publish `tags=tags or []`；`api.py:500` `post.tags = tags or []`（edit）；`api.py:1447` 公告 `announcement_tags`
- `[R]` `api.py:532` `_Post.tags.contains(tag)`（JSON 数组元素精确匹配，**不是子串**）；`api.py:1216` CSV `";".join(p.get("tags") or [])`；`api.py:1228` JSON 原样
- `[UI]` `home.py:139` 编辑弹窗默认值 `[t for t in (post["tags"] or []) if t in tags]`；`home.py:281-286` `if post["tags"]:` → badge/join；`home.py:283` `" ".join(f":blue-badge[{tag}]")`（**依赖元素是 str**）
- `[UI]` `bookmarks.py:38-39` 同款 join；`admin.py:110` `flat_tags = [t for p in posts_all for t in (p.get("tags") or [])]` → 标签分布图；`admin.py:250` `st.json({"tags": p["tags"]})`
- `[UI]` `publish.py:71-77` `st.multiselect(tags, default=[tags[0]])`；`publish.py:84-86` 预览 badge
- 契约：JSON 数组、元素来自 `const.tags`（18 个）；测试断言 `p["tags"] == ["趣事分享"]`

#### `comments` — JSONField(default=list)，**双重语义**
- 原始存储：`list[int]`（评论 ID）
- `Post.get()` 返回：`list[str]`，每个元素是 `json.dumps({id,userid,content,created_at})`（`api.py:680-690, 697`）
- `[W]` `api.py:736` `post.comments = (post.comments or []) + [cid]`（add_comment）；`api.py:760` `del post.comments[comment_index]`（delete_comment）；`api.py:692-696` `Post.get()` 清理失效 ID 并 `post.save()`（**读操作带写副作用**）；`api.py:1341-1342` `post.comments.remove(comment_id)`（`delete_comment_by_id`）
- `[R]` `api.py:667` `post.get("comments") or []`；`api.py:705-707` `cids[-1]`（`get_last_comments`，要求元素为 int）；`api.py:715-717`；`api.py:745-746`/`757-758` `post.comments[comment_index]`（按**索引**定位）；`api.py:1314-1316` `post_comments.index(comment["id"])`；`api.py:1218` CSV `len(...)` → `comments_count`
- `[UI]` `home.py:649-659` `comments = post["comments"]` → `json.loads(c)["userid"]`、`json.loads(comment)`；`home.py:664` 用 `enumerate` 的 `index` 调 `Post.edit_comment/delete_comment`；`home.py:696` 
- `[UI]` `admin.py:252` `"comments_count": len(p.get("comments", []))`
- 测试：`test_data_layer.py:141-142` `len(p["comments"]) == 1`、`"第一条评论" in p["comments"][0]`；`:155` `== []`
- **最高风险字段**：任何改成关联表的重构必须同时改 `api.py:658-698, 700-718, 741-763`、`home.py:649-702`、`admin.py:252`、`tests/test_data_layer.py:132-155`

#### `views` — IntegerField(default=0)
- `[W]` `api.py:655` `_Post.update(views=_Post.views + 1).where(_Post.id == postid).execute()`（原子 +1）
- `[R]` `api.py:606` `order_by(_Post.views.desc(), ...)`（按浏览量排序）；`api.py:647` `sum(p.get("views", 0) ...)`（作者统计）
- `[EXP]` CSV **不含** views（见 `api.py:1196-1207` 列清单）；posts JSON 含
- `[UI]` `home.py:291` `{post.get('views', 0)} 次浏览`；`home.py:298` 表格"浏览量"；`bookmarks.py:43`
- 测试：`test_data_layer.py:118` `p1["views"] == 0`；`:180-182` `== 2`；`:275-277/480` 直接 `api._Post.update(views=...)`

---

### 1.3 `_Comment`（`api.py:134-141`）

#### `id` — AutoField
- `[W]` `api.py:727-733` `_Comment.create(...)` → 返回 `comment.id` 写入 `_Post.comments`
- `[R]` `api.py:673` `.where(_Comment.id.in_(comment_ids))`；`api.py:712` `.in_(ids)`；`api.py:748` update where；`api.py:759` delete where；`api.py:1335` `_Comment.get_or_none(_Comment.id == comment_id)`；`api.py:1371` delete where
- `[FK]` ← `_Post.comments` 数组；← `_Report.commentid`；`admin.py:322/423` 按 id 删；`admin.py:310-318` 输入"评论 ID"
- `[EXP]` `api.py:1241/1244` 评论 CSV 第 1 列
- 关键：**评论按 `_Post.comments` 下标操作**（`edit_comment(postid, comment_index, ...)`），不按 id 操作；id 只在管理员工具与举报里用

#### `postid` — IntegerField
- `[W]` `api.py:728` create
- `[R]` `api.py:488` `_Comment.delete().where(_Comment.postid == postid)`（删文章级联）；`api.py:1313` `posts.get(comment["postid"])`；`api.py:1338` `_Post.get_or_none(_Post.id == comment.postid)`；`api.py:1364` `c["postid"] not in existing_ids`（孤立评论）
- `[EXP]` `api.py:1241/1244` 评论 CSV 第 2 列
- `[UI]` `api.py:1319-1329` 组装 `comment_summary` 的 `post_id`/`post_title` → `admin.py:305` DataFrame；`admin.py:322` 删除
- `[FK]` → `_Post.id`（无约束）

#### `userid` — CharField
- `[W]` `api.py:729` create
- `[R]` `api.py:684` `comment["userid"]`（hydrate）；`api.py:1354` `_Comment.userid.contains(keyword)`（搜索评论）
- `[EXP]` `api.py:1241/1244` 评论 CSV 第 3 列
- `[UI]` `home.py:656` `json.loads(c)["userid"]`；`home.py:660/665`；`home.py:674/680` 权限判定；`home.py:207` 举报 `target_userid=comment["userid"]`
- `[UI]` `api.py:1325` → `comment_summary` 的 `userid`

#### `content` — TextField
- `[W]` `api.py:730` create；`api.py:748` `_Comment.update(content=content).where(...)`
- `[R]` `api.py:685` hydrate；`api.py:1354` `.contains(keyword)`
- `[EXP]` `api.py:1241/1244` 评论 CSV 第 4 列
- `[UI]` `home.py:196` `comment['content'][:60]`；`home.py:208` `[:200]` 举报摘要；`home.py:670` `highlight_mentions(comment["content"])`；`home.py:693` 编辑弹窗回填；`api.py:1326`

#### `created_at` — CharField
- `[W]` `api.py:731` `datetime.now().strftime("%Y-%m-%d %H:%M:%S")`
- `[R]` `api.py:686` hydrate；`api.py:1244` CSV 第 5 列
- `[UI]` `home.py:669` `st.caption(comment.get("created_at",""))`；`home.py:887` 最新评论；`api.py:1327`
- 测试：`test_data_layer.py:146` 正则 `\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}`

---

### 1.4 `_Like` / `_Bookmark`（`api.py:144-151`）

两张表结构完全相同，仅语义不同。

#### `postid` — IntegerField
- `[W]` `api.py:869` `_Like.create(postid=postid, userid=userid)`；`api.py:899` `_Bookmark.create(...)`
- `[R]` `api.py:864` `_Like.get_or_none((_Like.postid == postid) & (_Like.userid == userid))`；`api.py:876`；`api.py:883` `count`；`api.py:892/906/915`
- `[R]` `api.py:643-644` `_Like.select().where(_Like.postid.in_(post_ids)).count()`（作者统计）；`api.py:576` `group_by(model.postid)`（排序聚合）
- `[FK]` → `_Post.id`（无约束）
- `[UI]` `home.py:345` `Like.count(post['id'])`；`home.py:360` `Bookmark.count(post['id'])`

#### `userid` — CharField
- `[W]` 同上 create
- `[R]` `api.py:864/876/892/906` 条件；`api.py:920` `get_bookmarked_post_ids` 返回 `[b.postid ...]`
- `[UI]` `home.py:249-279`、`home.py:341-368`、`bookmarks.py:26`
- `[FK]` → `_User.userid`（无约束）
- 备注：`AGENT.md:181` 明说**无唯一约束**，幂等靠 `get_or_none`。并发下有重复风险。

---

### 1.5 `_Report`（`api.py:154-168`）

#### `id` — AutoField
- `[W]` `api.py:962-971` `_Report.create(...).id`
- `[R]` `api.py:976` `Report.get`；`api.py:981` `order_by(_Report.id.desc())`；`api.py:1006` handle where
- `[UI]` `admin.py:404-425` `key=f"rep_ok_{r['id']}"` 等

#### `postid` — IntegerField(null=True)
- `[W]` `api.py:963` create
- `[R]` `api.py:957/959` 去重条件
- `[UI]` `admin.py:392` `is_post = bool(r.get("postid")) and not r.get("commentid")`（**null 判定决定举报类型**）；`admin.py:415` `Post.delete(r["postid"])`
- `[FK]` → `_Post.id`（可空）

#### `commentid` — IntegerField(null=True)
- `[W]` `api.py:964`
- `[R]` `api.py:957` 去重条件
- `[UI]` `home.py:206` `commentid=int(comment["id"])`；`admin.py:421-423` `delete_comment_by_id(r["commentid"])`
- `[FK]` → `_Comment.id`（可空）
- 契约：null 与非 null 是语义分界（举报帖子 vs 举报评论），`api.py:951-959` 注释明确

#### `target_userid` — CharField
- `[W]` `api.py:965`；`[R]` 无查询；`[UI]` `home.py:183/207` 传入；`admin.py:396` 展示

#### `reporter_userid` — CharField
- `[W]` `api.py:966`；`[R]` `api.py:953` 去重条件；`[UI]` `home.py:181/204` `state["userid"]`；`admin.py:395` 展示

#### `content_preview` — TextField(default="")，截断 500
- `[W]` `api.py:967` `(content_preview or "")[:500]`；`[UI]` `admin.py:398-399` `r.get("content_preview")`；`home.py:184/208` 生成

#### `reason` — TextField()
- `[W]` `api.py:947-949` 去空白、空则拒绝；`api.py:968`；`[UI]` `admin.py:400`

#### `status` — CharField(default="pending")，枚举 pending/handled/dismissed
- `[W]` `api.py:969` `"pending"`；`api.py:1001` handle 写入 `handled|dismissed`
- `[R]` `api.py:954` 去重限定 pending；`api.py:983` `get_all(status)`；`api.py:991` `count(status)`
- `[UI]` `admin.py:370-372` 三个 `Report.count(...)`；`admin.py:378` `status_map`；`admin.py:401` `r["status"] == "pending"`；`admin.py:431` `status_map[r['status']]`
- 契约：`handle` action 白名单 `("handled","dismissed")`（`api.py:997`）

#### `handled_by` — CharField(default="")
- `[W]` `api.py:1002`；`[UI]` `admin.py:432` `{r['handled_by'] or '—'}`；测试 `test_data_layer.py:429` `r["handled_by"] == "admin"`

#### `handled_at` — CharField(default="")
- `[W]` `api.py:1003` 时间字符串；`[UI]` `admin.py:432`；`[EXP]` 无

#### `note` — TextField(default="")，截断 500
- `[W]` `api.py:1004` `(note or "")[:500]`；`[UI]` `admin.py:433` `f" · {r['note']}"`；`home.py:416-418` `"已删除文章"` / `"已删除评论"`

#### `created_at` — CharField
- `[W]` `api.py:970`；`[UI]` `admin.py:400`

---

### 1.6 `_Draft`（`api.py:171-185`）

#### `id` — AutoField
- `[W]` `api.py:1038-1046` create 返回 id；`api.py:1028-1037` 更新已有
- `[R]` `api.py:1029` `(_Draft.id == draft_id) & (_Draft.userid == userid)`；`api.py:1063`；`api.py:1073`
- `[UI]` `publish.py:38` `key=f"load_draft_{d['id']}"`；`publish.py:46` `state["editing_draft_id"] = d["id"]`；`publish.py:49/129`

#### `userid` — CharField
- `[W]` `api.py:1039`；`[R]` `api.py:1029/1053/1063/1073/1081`（全部限定本人）
- `[UI]` `publish.py:29` `Draft.get_drafts(userid)`；`publish.py:49/129/139`
- 越权契约：`get_draft/delete` 返回 None/False（`test_data_layer.py:516-517`）

#### `title` — TextField(default="")
- `[W]` `api.py:1031/1040`；`[UI]` `publish.py:36` `d.get('title') or '（无标题）'`；`publish.py:40` 回填 `publish_title`
- 测试：`test_data_layer.py:504`

#### `content` — TextField(default="")
- `[W]` `api.py:1032/1041`；`[UI]` `publish.py:41` 回填 `publish_content`

#### `tags` — JSONField(default=list)
- `[W]` `api.py:1033/1042`；`[UI]` `publish.py:42-43` `[t for t in (d.get("tags") or []) if t in tags]`
- 测试：`test_data_layer.py:505` `d["tags"] == ["趣事分享"]`

#### `attpassword` — CharField(default="")，**明文**
- `[W]` `api.py:1034/1043` 直接存 `attpassword`（**未 sha256**）
- `[UI]` `publish.py:45` 回填 `publish_attpassword`
- 风险：与 `_Post.attpassword` 的哈希语义不一致；发布时 `Post.publish` 才 hash（`api.py:468-469`）

#### `created_at` — CharField
- `[W]` `api.py:1044` 仅创建时；**全项目无读取点**

#### `updated_at` — CharField
- `[W]` `api.py:1035`（更新）/`api.py:1045`（创建）；`[UI]` `publish.py:37` `c1.caption(d.get("updated_at", ""))`
- 注意：`get_drafts` 按 `_Draft.id.desc()` 排序（`api.py:1054`），不是 `updated_at`，与 docstring「按更新时间倒序」不符

---

## 2. `pages/*.py` → `api.py` 公共接口清单

> 全部为顶层直接调用（无函数入口）；每个 rerun 重新执行。

### 2.1 `pages/home.py`（imports `api.py:27-28`）

| 接口 | 调用点 | 参数形态 | 返回值使用 |
| --- | --- | --- | --- |
| `User()` | `86` | — | 实例 `user` |
| `user.get_config(userid)` | `92, 231, 379, 738` | `state["userid"]` / `post["authorid"]` / URL `user_id` | `dict|None`；键访问 `["avatar"],["username"],["role"],["created_at"]`，`.get("userid"/"role"/"username"/"description")`；`None` 时走 `home.py:93-101` 兜底 dict（6 键） |
| `User.get_configs(userids)` | `655, 852` | 生成器 `json.loads(c)["userid"]` / `p["authorid"]` | `dict[userid→dict]`；`.get()` 取单个，键 `avatar/username` |
| `user.modify(...)` | `429, 456` | kwargs `username/description/avatar`；`password/new_password` | `bool` 决定 toast/error |
| `Post.delete(post_id)` | `113` | int | `bool` |
| `Post.edit(post_id, title, content, tags)` | `148` | `post["id"]` | `bool` |
| `Post.add_view(postid)` | `508` | `int(post_id)` | 无返回使用 |
| `Post.get(postid)` | `510` | `int(post_id)` | `dict|None`；随后 `.get("attachments"/"attpassword"/"authorid"/"index"/"comments")` 与 `["content"],["title"],["id"],["created_at"],["tags"]` |
| `Post.get_by_author(authorid)` | `476` | URL `user_id` | `list[dict]`；`p[:50]`，键 `content/id` |
| `Post.get_author_stats(authorid)` | `394` | URL `user_id` | `dict`；4 个键 `post_count/views/likes/bookmarks` 直接进 `st.metric` |
| `Post.add_comment(postid, userid, content)` | `642` | — | 返回 id 未使用 |
| `Post.edit_comment(postid, index, content)` | `164` | `int(post_id)` + `enumerate` 下标 | `bool` |
| `Post.delete_comment(postid, index)` | `696` | 同上 | `bool` |
| `Post.count_filtered(...)` | `817` | kwargs `keyword/tag/author/start_date/end_date` | `int` → 页数 |
| `Post.get_filtered_paginate(...)` | `837` | + `page/page_size=5/sort_by` | `list[dict]`；键 `id/authorid/title/content/created_at/attachments/attpassword/tags/views/comments` |
| `Post.get_last_comments(posts)` | `853` | 上述 list | `dict[post_id→comment dict]`；`.get(post["id"])`，键 `userid/content/created_at` |
| `Like.is_liked/toggle/count` | `249/276/345`, `342/350` | `(post["id"], state["userid"])` | `bool`/`bool`/`int`（`int` 直接进按钮文案 f-string） |
| `Bookmark.is_bookmarked/toggle/count` | `250/279/360`, `355/365` | 同上 | 同上 |
| `Report.add(reporter, *, postid, commentid, target_userid, content_preview, reason)` | `180, 203` | 关键字实参 | `int|None`；truthy 判定 |
| `Attachment.get_thumbnail_base64(saved_name, max_width=200)` | `321` | `att.get("saved_name")` | `str` → `data:image/jpeg;base64,...` |
| `Attachment.get_file(saved_name)` | `550, 896` | `att.get("saved_name", "")` | `bytes`；空=丢失 |
| `get_avatar_bytes(avatar_name)` | `74, 383` | `_config["avatar"]` / `userconfig["avatar"]` | `bytes` → base64 |
| `save_avatar(uploaded_file)` | `428` | Streamlit UploadedFile | `dict`；取 `meta.get("path")` |
| `format_size(size)` | `594, 908` | `att.get("size", 0)` | `str` |
| `sha256(value)` | `104, 538, 539` | `""` / 用户输入 | `str`（附件密码比较） |

### 2.2 `pages/admin.py`（imports `api.py:26-33`）

| 接口 | 调用点 | 返回值使用 |
| --- | --- | --- |
| `User().count()` | `47, 150` | `int` → metric |
| `User().get_all()` | `77, 151` | `list[dict]` → `pd.DataFrame(...).astype(str)`；**剔除 `password`/`secret_key` 两列**（`153-155`） |
| `User().get_config(uid)` | `164` | `dict` → `st.json(cfg)`（含 role/avatar/created_at 等 6 键） |
| `User().modify(uid, None, role=..., admin_secret_key=...)` | `179-184` | `bool` |
| `User().delete(uid, None, admin_secret_key=...)` | `209-210` | `bool` |
| `Post().count()` | `48, 230` | `int` |
| `Post().get_all()` | `76, 231` | `list[dict]`；读 `tags`（`110`）、`authorid`（`122`）、`created_at`（`84` via `_daily_counts`）；DataFrame 展示全字段（含 `attpassword/comments/attachments` 原始 JSON） |
| `Post().get(int(pid))` | `240` | `dict|None`；读 `id/title/authorid/content/created_at/tags/attachments/comments` |
| `Post().delete(int(pid))` | `278, 415` | `bool` |
| `Report.count(status)` | `370-372` | `int` ×3 |
| `Report.get_all(status=...)` | `386` | `list[dict]`；逐条读 `postid/commentid/target_userid/reporter_userid/content_preview/reason/created_at/status/handled_by/handled_at/note/id` |
| `Report.handle(id, userid, action, note)` | `406, 410, 416, 424` | `bool`（未使用） |
| `delete_comment_by_id(cid)` | `322, 423` | `bool` |
| `delete_orphaned_comments()` | `357` | `int` |
| `get_orphaned_comments()` | `352` | `list[dict]` → `pd.DataFrame` |
| `search_comments(keyword)` | `343` | `list[dict]` → DataFrame |
| `get_comment_summary()` | `45, 78, 299` | `list[dict]`（7 键：comment_id/post_id/post_title/comment_index/userid/content/created_at）；长度当"评论总数"；DataFrame 展示 |
| `get_attachment_stats()` | `44` | `dict` 2 键 `count/total_size` |
| `get_all_attachments()` | `439` | `list[dict]`（附件 5 键 + post_id/post_title）→ DataFrame |
| `get_orphaned_attachments()` | `445` | `list[str]` 文件名 |
| `delete_orphaned_attachments()` | `450` | `int` |
| `export_users_csv/json()` | `518, 525` | `bytes` → download_button |
| `export_posts_csv/json()` | `535, 542` | 同上 |
| `export_comments_csv/json()` | `553, 560` | 同上 |
| `execute_sql(query)` | `475` | `list[dict]` → `st.write` |
| `sha256(text)` | `486` | `str` → `st.code` |
| `ATTACHMENTS_DIR` | `460, 570` | 拼路径；`st_file_browser(ATTACHMENTS_DIR, ...)` |
| `format_size(n)` | `51, 495` | `str` |

### 2.3 `pages/publish.py`（imports `api.py:17`）

| 接口 | 调用点 | 返回值使用 |
| --- | --- | --- |
| `Draft.get_drafts(userid)` | `29` | `list[dict]`；读 `title/updated_at/content/tags/attpassword/id`（`36-49`） |
| `Draft.delete(userid, draft_id)` | `49, 129` | `bool`（未使用） |
| `Draft.save(userid, title, content, tags, attpassword, draft_id=)` | `139-146` | `int|None` → `state["editing_draft_id"]` |
| `Attachment.save(uploaded_file)` | `113` | `dict` → 收集进 `_attachments` 列表，整体传给 `Post.publish` |
| `Post.publish(authorid, title, content, attachments, attpassword, tags)` | `118-125` | `int`（未使用） |

### 2.4 `pages/bookmarks.py`（imports `api.py:14`）

| 接口 | 调用点 | 返回值使用 |
| --- | --- | --- |
| `Bookmark.get_bookmarked_post_ids(userid)` | `26` | `list[int]` → 直接传给 `post_ids` |
| `Post.get_filtered_paginate(post_ids=..., page=1, page_size=100)` | `31` | `list[dict]` |
| `User.get_configs(p["authorid"] for p in posts)` | `32` | `dict[userid→dict]`；`.get()` 取 `username` |
| 字段消费 | `37-53` | `post['title'] / .get("tags") / ['created_at'] / .get("views",0) / ['content'] / ['id'] / ['authorid']` |

### 2.5 `pages/register.py` / `pages/login.py` / `pages/logout.py` / `pages/image_generate.py`

| 页面 | 接口 | 调用点 | 说明 |
| --- | --- | --- | --- |
| `register.py` | `User().register(userid, username, password)` | `52` | `bool` → 真则继续 |
| `register.py` | `User.get_secret_key(userid)` | `53` | `str` → `cookies["secretkey"]` / `state["secretkey"]` |
| `login.py` | `User().login(userid, password)` | `32` | `bool` |
| `login.py` | `User.get_secret_key(userid)` | `33` | 同上 |
| `logout.py` | — | — | **不 import api**，只清 session/cookies（`14-19`） |
| `image_generate.py` | — | — | **不 import api，完全不读写数据库**（只读写 `st.session_state.image_results`，`41-42, 173-178`） |

### 2.6 `main.py` / `api2.py`

| 位置 | 接口 | 使用 |
| --- | --- | --- |
| `main.py:31` | `from api import User` | — |
| `main.py:164` | `user = User()` | 实例 |
| `main.py:176-177` | `user.get_config(state["userid"]) or {}` → `.get("role") == admin` | 决定导航是否出现管理后台/图片生成 |
| `api2.py:17,19` | `from api import User; user = User()` | 模块级实例 |
| `api2.py:25` | `user.check(state.get("userid",""), state.get("password",""))` | `bool`；**每次 rerun 真实查库**，禁止缓存 |

---

## 3. `tests/test_data_layer.py` 接口契约（25 个用例）

> 测试直接 import `_User/_Post/_Comment` 等**私有模型类**（`test_data_layer.py:56,134,245,275-277,480`）并直接调 `api._Post.update(...)`；`conftest.py:39-47` 还按类名清表。

| # | 用例 | 依赖函数 | 断言的字段/格式 |
| --- | --- | --- | --- |
| 1 | `test_register_and_login` `41` | `register/exists/login` | `register` 返回 `True`；`exists` truthy；错误密码/`None` 返回 `False` |
| 2 | `test_register_duplicate_fails` `49` | `register` | 重复 userid → `False` |
| 3 | `test_old_sha256_password_auto_upgrade` `54` | `_User.create`、`login`、`_User.get`、`sha256`、`verify_password` | 旧 `sha256("legacy123")` 登录成功；`_User.get(...).password.startswith("pbkdf2$")`；`verify_password` 往返 |
| 4 | `test_hash_verify_roundtrip` `72` | `hash_password/verify_password/sha256` | `hash_password` 以 `"pbkdf2$"` 开头；兼容旧 sha256 |
| 5 | `test_change_password_requires_original` `84` | `check/modify/login` | 原密码错 → `modify` 返回 `False` 且密码不变；原密码对 → `True` |
| 6 | `test_post_publish_auto_increment_id` `109` | `Post.publish/get` | `publish` 返回 `int` 且递增不重复；`Post.get(id)["title"]`、`["views"] == 0` |
| 7 | `test_post_edit_and_delete` `121` | `Post.edit/get/delete` | `p["title"]`、`p["tags"] == ["趣事分享"]`；删除后 `Post.get` 为 `None` |
| 8 | `test_comment_add_edit_delete` `132` | `Post.add_comment/get/edit_comment/delete_comment`、`_Comment.get` | `p["comments"]` 长度 1；`"第一条评论" in p["comments"][0]`（**依赖 JSON 字符串**）；`_Comment.created_at` 正则；`edit_comment(pid,0)` `True` / `(pid,99)` `False`；删后 `comments == []` |
| 9 | `test_like_and_bookmark_toggle` `158` | `Like/Bookmark.toggle/is_liked/is_bookmarked/count` | toggle 真→假、count 1→0 |
| 10 | `test_views_increment` `175` | `Post.add_view/get` | `views` 0→2 |
| 11 | `test_count_filtered_and_paginate_consistent` `188` | `Post.count_filtered/get_filtered_paginate`、`Bookmark` | 关键词命中标题/内容；`tag` 精确匹配；`post_ids` + `tag` 组合；分页与总数一致；`["id"]` 键 |
| 12 | `test_count_filtered_by_date_range` `228` | 同上；`api._Post.update(created_at=...).execute()` | `created_at` **字符串**写入并做 `>=/<=` 比较；起止均含 |
| 13 | `test_get_filtered_paginate_sort` `264` | `get_filtered_paginate(sort_by=)` | `views`/`date`/`likes`/`bookmarks` 四种排序结果 id 顺序 |
| 14 | `test_get_by_author` `306` | `Post.get_by_author` | 只含该作者；`p["authorid"]`；id 倒序 |
| 15 | `test_attachment_save_get_and_traversal_blocked` `322` | `Attachment.save/get_file` | `meta["saved_name"]` 匹配 `[0-9a-f]{32}\.txt`；`get_file` 往返；`../../...` / `../data.db` 返回 `b""` |
| 16 | `test_attachment_thumbnail` `334` | `Attachment.get_thumbnail_bytes` | 返回 JPEG 魔术字节 `\xff\xd8` |
| 17 | `test_avatar_compression` `347` | `save_avatar/get_avatar_bytes/MAX_AVATAR_DIM` | `saved_name` 以 `.jpg` 结尾、`type == "image/jpeg"`、体积减半、1:1、边长 ≤512 |
| 18 | `test_report_add_and_deduplicate` `377` | `Report.add/count` | 同人同目标 pending 去重返回 `None`；不同人可重复；`count("pending")` |
| 19 | `test_report_handle_and_status` `415` | `Report.handle/get` | `status == "handled"`、`handled_by == "admin"`；非法 action `"weird"` → `False` |
| 20 | `test_report_comment` `434` | `Report.add/get` | `r["commentid"] == cid`、`r["postid"] == pid` |
| 21 | `test_count_filtered_by_author` `456` | `count_filtered/get_filtered_paginate` | `author=` 模糊（`contains`）；无匹配 → 0；与 keyword 组合 |
| 22 | `test_get_author_stats` `473` | `Post.get_author_stats` | 4 键精确值；`views` 通过直接 UPDATE 设置 |
| 23 | `test_draft_save_load_delete` `497` | `Draft.save/count/get_draft/delete` | 返回 id；`d["title"]`、`d["tags"]`；更新返回同 id；他人读 `None`、删 `False` |
| 24 | `test_report_post_and_comment_are_distinct` `522` | `Report.add` | 帖子 vs 评论举报互不误判；同评论重复 → `None` |
| 25 | `test_execute_sql_readonly` `566` | `execute_sql` | `SELECT 1 AS x` → `[{"x": 1}]`；`"  select count(*) as n from _user  ;"` 可执行；`DELETE/UPDATE/DROP/INSERT/""` 抛 `ValueError` |

**conftest 契约**（`tests/conftest.py`）：
- `conftest.py:26-30` 先 `chdir(tempfile.mkdtemp())` 并写 `config.toml`（`[admin] userid="testadmin", username="Test Admin"`，无密码 → 不创建管理员账号）再 import api
- `conftest.py:39-47` 清表顺序/类名：`api._User, api._Post, api._Comment, api._Like, api._Bookmark, api._Report, api._Draft` → **类名 7 个全部硬编码**
- `conftest.py:56-58` monkeypatch `api.ATTACHMENTS_DIR` / `api.AVATARS_DIR` → **模块级变量名硬编码**
- `conftest.py:63-73` `FakeUpload` 提供 `name/type/size/getvalue()` → `Attachment.save`/`save_avatar` 只能依赖这 4 个属性

---

## 4. peewee 表达式 / 原生 SQL 使用点

### 4.1 原生 SQL
| 位置 | 语句/形式 | 说明 |
| --- | --- | --- |
| `api.py:80` | `cursor = db.execute_sql(stripped)` | **唯一**原生 SQL 入口；`execute_sql` 白名单 `SELECT/WITH/EXPLAIN/PRAGMA`（`api.py:77`） |
| `api.py:81-82` | `cursor.description` / `dict(zip(columns, row))` | 返回列名 = **数据库真实列名**（`_post`、`attpassword` 等） |
| `admin.py:471` | placeholder `"例如：SELECT * FROM _user"` | 表名 `_user` 对用户可见 |
| `tests/test_data_layer.py:570` | `"select count(*) as n from _user"` | 表名硬编码进测试 |

### 4.2 查询/表达式清单
| 位置 | 表达式 | 语义 |
| --- | --- | --- |
| `api.py:284` | `_User.get_or_none(_User.userid == ...)` | 引导幂等 |
| `api.py:320` | `_User.get_or_none(_User.userid == userid)` | 登录 |
| `api.py:342` | `_User.get_or_none(_User.secret_key == secret_key)` | 管理员密钥 |
| `api.py:350` | `_User.get_or_none(_User.userid == userid)` | exists |
| `api.py:355` | `_User.select().dicts()` | 全量用户 |
| `api.py:387` | `.where(_User.userid.in_(ids))` | 批量配置 |
| `api.py:435` | `_User.select().count()` | 用户计数 |
| `api.py:488-489` | `_Comment.delete().where(postid==)` / `_Post.delete().where(id==)` | 级联删 |
| `api.py:508` | `_Post.select().dicts()` | 全量文章 |
| `api.py:528-530` | `_Post.title.contains() \| _Post.content.contains()` | 关键词 |
| `api.py:532` | `_Post.tags.contains(tag)` | **JSONField 元素精确匹配** |
| `api.py:534` | `_Post.id.in_(post_ids)` | 收藏过滤 |
| `api.py:536-543` | `_User.userid.contains \| _User.username.contains` → `_Post.authorid.in_` | 作者模糊 |
| `api.py:546` | `_Post.id == -1` | 恒空查询 |
| `api.py:549/553` | `created_at >= "... 00:00:00"` / `<= "... 23:59:59"` | 日期区间（字符串比较） |
| `api.py:567-569` | `.count()` | 计数口径 |
| `api.py:576-578` | `model.select(model.postid, fn.COUNT(model.userid).alias("cnt")).group_by(model.postid).dicts()` | 点赞/收藏聚合 |
| `api.py:604-609` | `.order_by(_Post.views.desc(), _Post.id.desc()).paginate(page,page_size).dicts()` | 浏览量排序 |
| `api.py:619-622` | `.order_by(_Post.created_at.desc(), _Post.id.desc()).paginate(...)` | 日期排序 |
| `api.py:628-632` | `.where(_Post.authorid == authorid).order_by(_Post.id.desc()).dicts()` | 作者文章 |
| `api.py:638-644` | `_Post...dicts()`、`_Like.postid.in_(post_ids)`、`_Bookmark...` | 作者统计 |
| `api.py:655` | `_Post.update(views=_Post.views + 1).where(...)` | 原子 +1 |
| `api.py:664` | `_Post.select().where(_Post.id == _id).dicts().get_or_none()` | 详情 |
| `api.py:673` | `_Comment.id.in_(comment_ids)` | 批量评论 |
| `api.py:692-696` | `_Post.get_or_none(...)` + `post_obj.save()` | **读时写**（清理失效评论 id） |
| `api.py:712` | `_Comment.id.in_(ids)` | 最新评论 |
| `api.py:748` | `_Comment.update(content=...).where(_Comment.id == cid)` | 编辑评论 |
| `api.py:759` | `_Comment.delete().where(_Comment.id == cid)` | 删评论 |
| `api.py:864/876` | `get_or_none((postid==) & (userid==))` | 点赞幂等 |
| `api.py:883` | `_Like.select().where(postid==).count()` | 点赞数 |
| `api.py:892/906` | 同 `_Bookmark` | 收藏幂等 |
| `api.py:915` | `_Bookmark...count()` | 收藏数 |
| `api.py:920` | `[b.postid for b in _Bookmark.select().where(userid==)]` | 收藏 id |
| `api.py:953-960` | `(_Report.reporter_userid==) & (status=="pending") & (postid==) & (_Report.commentid.is_null() / ==)` | 举报去重（`is_null()` 关键） |
| `api.py:976` | `_Report.select().where(id==).dicts().get_or_none()` | 举报详情 |
| `api.py:981-984` | `.order_by(_Report.id.desc())` + `status` 过滤 | 举报列表 |
| `api.py:989-992` | `.count()` + status | 举报计数 |
| `api.py:999-1007` | `_Report.update(status=..., handled_by=..., handled_at=..., note=...).where(id==)` | 处理举报 |
| `api.py:1029` | `_Draft.get_or_none((id==) & (userid==))` | 草稿更新 |
| `api.py:1038-1046` | `_Draft.create(...)` | 新建草稿 |
| `api.py:1051-1056` | `.where(userid==).order_by(_Draft.id.desc()).dicts()` | 草稿列表 |
| `api.py:1061-1066` | `.where((id==) & (userid==)).dicts().get_or_none()` | 单草稿 |
| `api.py:1071-1075` | `_Draft.delete().where((id==) & (userid==)).execute()` | 删草稿（返回 >0） |
| `api.py:1081` | `_Draft.select().where(userid==).count()` | 草稿数 |
| `api.py:1172/1183` | `_User.select().dicts()` | 导出 |
| `api.py:1208/1228` | `_Post.select().dicts()` | 导出 |
| `api.py:1242/1253` | `_Comment.select().dicts()` | 导出 |
| `api.py:1267/1277/1286` | `_Post.select().dicts()` | 附件统计/列表/孤立 |
| `api.py:1310` | `_Post.select()` | 全量模型实例（`post.id`/`post.title`/`post.comments`） |
| `api.py:1312` | `_Comment.select().dicts()` | 评论摘要 |
| `api.py:1335` | `_Comment.get_or_none(_Comment.id == comment_id)` | 按 id 删 |
| `api.py:1351-1358` | `_Comment.content.contains \| _Comment.userid.contains` + `order_by(id.desc())` | 搜索评论 |
| `api.py:1363` | `_Post.select(_Post.id)` | 孤立评论判定 |
| `api.py:1371` | `_Comment.delete().where(_Comment.id == c["id"]).execute()` | 删孤立评论 |
| `api.py:1397-1399` | `_Post.get_or_none((authorid==) & (title==))` | 公告幂等 |
| `tests/test_data_layer.py:245,275-277,480` | `api._Post.update(...).where(...).execute()` | 测试直改字段（假时间/浏览量） |

---

## 5. 风险点（按危险度排序）

### R1. `_Post.comments` 双语义 + 读时写（最高危）
- 存储 `list[int]`；`Post.get()` 返回 `list[str]`（JSON dumps），并**在读取时可能 `save()` 清理失效 id**（`api.py:692-696`）。
- UI 直接 `json.loads`：`home.py:656`、`home.py:659`。
- 列表页 `get_filtered_paginate` 返回原始 `int[]`，`get_last_comments` 依赖 `cids[-1]` 是 int（`api.py:705-707`）。
- 评论编辑/删除按**数组下标**而非 id：`api.py:741-763`，UI 传 `enumerate` 的 `index`（`home.py:664,696`）。
- 测试断言 JSON 字符串包含内容（`test_data_layer.py:141-142,155`）。
- 影响：任何把 `comments` 改成关联表/改成评论 id 主键操作的重构，必须同时改上述 9 处 + `admin.py:252`。

### R2. 用户/角色/密钥是安全边界，且被导出
- `role` 判定统一 `== const.admin`（`home.py:244,319,530,676,739`、`main.py:177`、`api.py:340,344`）；历史存在小写 `"user"`，不能用 `!= const.user`。
- `_User.secret_key` 直接换 ADMIN（`api.py:342-344`），且 `export_users_json` 原样导出全部列 → **password 哈希 + secret_key 泄露**（`api.py:1183-1185`）。CSV 已裁剪（5 列），JSON 未裁剪。
- `admin.py:153-155` 只对 DataFrame 展示剔除，不是数据层保证。

### R3. UI 直接渲染的字段（字段名/类型即契约）
| 字段 | UI 直接消费点 | 假设 |
| --- | --- | --- |
| `post.created_at` / `comment.created_at` / `user.created_at` / `draft.updated_at` | `home.py:290,297,388,669,887`、`bookmarks.py:42`、`publish.py:37`、`admin.py:388` | `YYYY-MM-DD HH:MM:SS` 字符串；`admin.py:62` 切 `[:10]` |
| `post.tags` | `home.py:139,281-286`、`bookmarks.py:38-39`、`admin.py:110`、`publish.py:84-86` | JSON 数组、元素 str、来自 `const.tags` |
| `post.views` | `home.py:291,298`、`bookmarks.py:43` | int，可 `.get(...,0)` |
| `post.attachments` | `home.py:521-624,890-908`、`admin.py:251,439-443` | list[dict]，5 键固定；`type` 前缀分派；`saved_name` 是文件名 |
| `post.attpassword` | `home.py:525,527-541,894` | sha256(salt="freelake") 字符串 |
| `post.title/content/authorid/id` | 全站 | 字符串/整数，标题进 ZIP 名（`home.py:578`） |
| `user.avatar` | `home.py:233,239,383,665` | 文件名；`default_avatar.jpeg` 特判 |
| `user.username/description/role` | `home.py:384,390,417,423` | — |
| `draft.title/content/tags/attpassword/updated_at` | `publish.py:36-45` | tags JSON 数组；attpassword **明文** |
| `report.*`（12 列） | `admin.py:390-434` | `postid/commentid` nullable 判类型 |

### R4. 主键/外键（逻辑外键，全无约束）
- `_Post.id` AutoField：被 `_Comment.postid`、`_Like.postid`、`_Bookmark.postid`、`_Report.postid` 与 `_Post.comments[]` 引用。
- `_User.userid`：被 6 处字符串字段引用；**无 unique 约束** → `register` 的 `exists()` 检查有竞态（`api.py:305-315`）。
- `_Comment.id`：被 `_Post.comments[]` 与 `_Report.commentid` 引用。
- 无 `ForeignKeyField`、无级联：`Post.delete` 手工删 `_Comment`（`api.py:488`），不删 `_Like/_Bookmark/_Report` → 孤儿点赞/收藏/举报。
- `_Report.commentid IS NULL` 是「举报文章 vs 举报评论」的唯一判别（`api.py:956-959`、`admin.py:392`）。
- `_Like`/`_Bookmark` 无 `UNIQUE(postid,userid)`，仅靠 `get_or_none` 幂等。

### R5. 导出契约（CSV/JSON）
| 导出 | 列/字段 | 风险 |
| --- | --- | --- |
| `export_users_csv` `api.py:1164-1176` | `userid,username,created_at,description,role` | 列名/顺序是外部契约；已排除 password/secret_key/avatar |
| `export_users_json` `api.py:1179-1186` | `_User.select().dicts()` **全列**（含 `id/password/avatar/secret_key`） | 敏感泄露 + 字段名契约 |
| `export_posts_csv` `api.py:1189-1221` | `id,authorid,title,content,created_at,tags,attachments_count,comments_count` | `tags` 用 `";".join`；两个 `_count` 是**计算列**（不落库）；不含 `views/attpassword` |
| `export_posts_json` `api.py:1224-1231` | `_Post.select().dicts()` **全列** | 含 `attpassword` 哈希、`attachments` JSON、`comments` **原始 int[]** |
| `export_comments_csv/json` `api.py:1234-1256` | `id,postid,userid,content,created_at` | 列名契约 |
| 编码 | 全部 `.encode("utf-8-sig")` | Excel BOM 契约 |

### R6. 空值/默认值/历史脏数据语义
- `_User.description` 默认 `"这个用户有点懒，什么也没留下~"`（`api.py:109`）vs `home.py:390` 兜底 `"这个用户很懒，什么也没留下~"`——**两套文案**。
- `_User.role` 默认小写 `"user"`（`api.py:111`），而 `const.user="USER"`；`_User.create` 由 `test_data_layer.py:59-65` 显式传 `"USER"`。
- `_Draft.attpassword` 明文 vs `_Post.attpassword` 哈希。
- `_Post.comments` 可能出现指向已删评论的 id（靠 `Post.get` 清理）。
- `_Report.handled_by/handled_at/note` 默认 `""`（非 NULL）。
- `_Post.created_at` 是字符串；`tests` 直接 UPDATE 成 `2024-..`，生产代码不得假设等于 `now`。

### R7. 其它硬编码
- 表名：`_user/_post/...`（`admin.py:471`、`tests:570`）。
- 模型类名 7 个：`conftest.py:39-47`。
- 模块级目录变量：`api.ATTACHMENTS_DIR/AVATARS_DIR`（`conftest.py:56-57`）。
- `ANNOUNCEMENT_POST_TITLE`（`api.py:1382`）与 `announcement_tags`（`const.py:35`）：公告幂等依据是「管理员 + 标题」，改标题会导致重新发布。
- 附件文件名 UUID32 + 原扩展名（`api.py:788`、`tests:326` 正则）。
- 头像统一 `.jpg` / `image/jpeg` / ≤512（`api.py:1108-1125`、`tests:362-371`）。

---

## 6. 重构可行性结论

### 6.1 硬契约（语义不可改；只能加兼容层）
1. **`_Post.id` 自增整数主键**：URL `?post_id=`、所有 `postid`/`commentid` 字段、测试 B5。
2. **`_User.userid` 字符串主键语义**与所有 `*userid` 字符串外键；`_Post.authorid`/`_Comment.userid`/`_Like.userid`/`_Bookmark.userid`/`_Report.*_userid`/`_Draft.userid` 的字符串类型。
3. **时间字段为 `YYYY-MM-DD HH:MM:SS` 字符串**（字典序即时间序、`[:10]` 切日期、`>=`/`<=` 字符串比较、测试正则）。
4. **`role` 与 `const.admin` 的比较方式**（`== admin`），默认值小写历史兼容。
5. **`_User.secret_key` 存在性与 `check_admin(secret_key)` 行为**（管理员登录/改角色/删用户的第二凭据）。
6. **`Post.get()` 返回 dict 的键名与 `comments` 的 JSON 字符串列表形态**（`home.py:649-702` 直接 `json.loads`）；`get_filtered_paginate` 返回 dict 的键名（`id/authorid/title/content/created_at/attachments/attpassword/tags/comments/views`）。
7. **评论按「文章内下标」操作**（`edit_comment/delete_comment(postid, comment_index)`），评论 id 只在管理员工具/举报使用。
8. **`attachments` 元素 5 键**（`original_name/saved_name/type/size/path`）与「只存文件名」。
9. **`attpassword = sha256(明文+"freelake")`** 且 UI 直接比较哈希（`home.py:538-539`）；`_Draft.attpassword` 目前是明文，改它要同步 `publish.py:45` 与 `Post.publish`。
10. **导出表头/列名/编码**：用户 CSV 5 列、文章 CSV 8 列（`tags` 用 `;` 连接、两个 `_count` 计算列）、评论 CSV 5 列；一律 `utf-8-sig`。
11. **`execute_sql` 只读白名单**与 `db.execute_sql` 实现（`tests:566-579`）。
12. **表名与 7 个模型类名**（`admin.py` 原始 SQL 示例、`conftest.py` 清表、现有 `data.db`）。
13. **`Attachment`/`save_avatar` 的 `FakeUpload` 4 属性契约**（`name/type/size/getvalue`）。

### 6.2 可安全重构（低风险，建议本轮做）
1. **给高频查询列加索引**：`_Post.authorid`、`_Post.created_at`、`_Comment.postid`、`_Like(postid,userid)`、`_Bookmark(postid,userid)`、`_User.userid` UNIQUE。不改数据/接口，仅 DDL；需注意 `create_tables(safe=True)` 不会自动加，要写显式迁移（`CREATE INDEX IF NOT EXISTS`）。
2. **`_Like`/`_Bookmark` 加 `UNIQUE(postid,userid)`**：接口不变；加之前需去重历史数据。
3. **抽出 `_Post.comments` 的兼容读取函数**（如 `get_raw_comment_ids(post)`），把 `api.py:667/705/745/758/1314` 的裸下标写法收敛——纯内部重构，不动存储。
4. **新增字段**（如 `_Post.updated_at`、`_Report.target_type`）：安全，但 `create_tables(safe=True)` 不会 ALTER 现有 `data.db`，必须手写 `ALTER TABLE ... ADD COLUMN` 兜底迁移；且新字段需有默认值。
5. **把 `_Draft.attpassword` 改为哈希**：需同步 `publish.py:45`（回填的是哈希而非明文，会导致密码门禁错误）——若做，必须同时改 UI 逻辑并补测试；无现有测试覆盖，属中风险。
6. **把 `_Post.tags` 保持 JSON、另建 `_Tag` 维度表做统计**：只加不删，`_Post.tags` 仍是真相源，UI 零改动。安全。
7. **`_Report.postid/commentid` 保持 nullable**：可加索引/注释，但不要拆成 `target_type+target_id`（会破 `admin.py:392` 与 `api.py:956-959` 去重）。

### 6.3 高风险/不建议本轮做
1. **把 `_Post.comments` 改成关联表**：收益是参照完整性，代价是 `Post.get` 输出格式、`home.py:649-702`、`admin.py:252`、`tests:132-155` 全部要改，且列表页/详情页两条读取路径语义不同。建议先加只读适配层，分两阶段迁移。
2. **把 `_Post.attachments` JSON 改成附件表**：会同时破 `get_attachment_stats/get_all_attachments/get_orphaned_attachments/delete_orphaned_attachments` 4 个管理工具 + `home.py:521-624` + CSV `attachments_count`。收益低（附件元数据与文章同生命周期）。
3. **把 `created_at` 改成 datetime 类型**：破字符串比较、`[:10]`、测试正则、导出格式。
4. **把 `_User.userid` 改成自增主键 + 单独 username**：破 URL `?user_id=`、所有字符串外键、`check_by_state`、`get_configs` 的 key。
5. **重命名模型类/表名**：破 `conftest.py:39-47`、`admin.py:471`、`tests:570`、现有 `data.db`。
6. **在 `api.py` 模块导入阶段加新的一次性写操作**：与 `tests/conftest.py` 的「先 chdir 再 import」契约冲突（`conftest.py:26-30`），会污染测试临时库或真实库。

### 6.4 迁移检查清单（重构后必须验证）
- `python -m pytest tests -q` 25 passed（本审计未运行，仅静态确认用例覆盖点）。
- 手工回归：登录→首页列表（三种排序）→详情（附件密码/ZIP/评论下标编辑删除）→发布→草稿→收藏→管理后台（用户/文章/评论/举报/附件/导出/SQL 工具）→个人主页指标。
- 导出文件列名与 BOM 不变。
- `data.db` 旧库能直接启动（`create_tables(safe=True)` + 默认值/迁移逻辑）。
