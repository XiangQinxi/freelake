# FreeLake 表现层审查（pages/ + main.py）

> 审查基线：git HEAD `37f9f485b1878f69c35cd919da0da0bfac93b30d`（2026-08-23 14:27:21 +0800「提供分辨率」），审查时间 2026-10-25 13:29（Asia/Shanghai）；纯静态阅读（Streamlit 1.61.1 / Python 3.13.5），未运行应用、未 import `api`。
> 范围：`main.py`、`pages/*.py`、`api2.py`、`.streamlit/config.toml`；`api.py` 仅作为接口签名参照。
> 共 30 条：P0 × 3、P1 × 18、P2 × 9。

## P0

### P0-1 Cookie 恢复不含 secretkey，管理员刷新/重开浏览器后改角色、删用户全部失败
- 位置：`main.py:67-70`（恢复）、`pages/admin.py:183`、`pages/admin.py:210`（使用）
- 证据：
  ```python
  # main.py:69-70
  state["userid"] = cookies.get("userid", "")
  state["password"] = cookies.get("password", "")
  ```
  ```python
  # pages/admin.py:183 / :210
  admin_secret_key=state["secretkey"],
  ```
  而 `pages/login.py:33-40`、`pages/register.py:53-60` 都把 `secretkey` 同时写进了 state 与 cookies。
- 影响：Cookie 自动登录（关浏览器重开、新标签页首轮）走的是 `main.py:69-70`，只恢复 userid/password。此时 `state` 里没有 `secretkey` 键 → `admin.py:183` 直接 `KeyError`，被 `admin.py:189-190` 的 `except Exception as e: st.error(f"修改失败：{e}")` 捕获，界面显示「修改失败：'secretkey'」；若 session 里恰好残留空串，则 `User.modify`/`User.delete` 的 `check_admin("")`（api.py:715-729）查不到人而恒为 False，显示「修改失败：密钥错误、用户不存在或登录状态已过期」。两条路径都让管理员在「刷新后管理用户」这一常规操作上 100% 失败。
- 建议：`main.py:70` 后补 `state["secretkey"] = cookies.get("secretkey", "")`；`admin.py:183`、`:210` 改用 `state.get("secretkey", "")` 并给出「请重新登录」的明确提示。更彻底的做法是不把权限凭据放浏览器：Cookie 只存随机会话 token，服务端表记录 token→userid 与角色（见 P0-3）。

### P0-2 「只读 SQL」白名单只校验首个关键字，`WITH ... DELETE` 可清库
- 位置：`pages/admin.py:466-476`（入口与提示），`api.py:79-94`（白名单实现）
- 证据：
  ```python
  # pages/admin.py:467-469 提示语
  "⚠️ 仅支持只读 SQL 查询（SELECT / WITH / EXPLAIN / PRAGMA），写操作已被拦截！"
  # pages/admin.py:475-476
  result = execute_sql(query)
  st.write(result)
  ```
  ```python
  # api.py:79-94
  stripped = (query or "").lstrip().rstrip(";").strip()
  head = stripped.split(None, 1)[0].upper() if stripped else ""
  if head not in ("SELECT", "WITH", "EXPLAIN", "PRAGMA"): raise ...
  ```
- 影响：SQLite 允许 `WITH` 直接修饰写语句，例如 `WITH x AS (SELECT 1) DELETE FROM _post` 首词是 `WITH`，白名单放行 → 一次误输入（或把该页面暴露给他人）就能清空文章/用户表。`PRAGMA` 也不是只读（如 `PRAGMA journal_mode=...`、`PRAGMA writable_schema=ON`）。此外查询结果无 `LIMIT`，`SELECT * FROM _post` 会把整表塞进 `st.write`，管理页瞬间卡死、worker 内存暴涨。
- 建议：换成真正的只读连接执行：`con = sqlite3.connect(db_path); con.execute("PRAGMA query_only=ON")`，或对连接设置 `con.set_authorizer(lambda *a: sqlite3.SQLITE_OK if a[0] in (SQLITE_SELECT, SQLITE_READ, SQLITE_FUNCTION) else sqlite3.SQLITE_DENY)`；白名单至少收紧为「`SELECT` 或（`WITH` 且不含 `INSERT/UPDATE/DELETE`）」，去掉 `PRAGMA`；结果用 `cursor.fetchmany(500)` 截断并在页面提示「已截断」。

### P0-3 Cookie 加密密钥硬编码回落，且 Cookie 里存的是明文密码
- 位置：`main.py:55-60`、`pages/login.py:35-37`、`pages/register.py:55-57`
- 证据：
  ```python
  # main.py:57-59
  password=os.environ.get("COOKIES_PASSWORD")
  or _secrets_get("cookies", {}).get("password")
  or "dev-insecure-fallback-change-me",
  ```
  ```python
  # pages/login.py:35-37（register.py:55-57 同形）
  cookies["userid"] = userid
  cookies["password"] = password
  cookies["secretkey"] = secretkey
  ```
- 影响：任何漏配 `COOKIES_PASSWORD` / `[cookies].password` 的部署（本地、演示机、忘记配置的线上）都在用公开可知的密钥加密 Cookie，而 Cookie 内容就是 userid + **明文密码** + `secretkey`。拿到 Cookie 的人（共享电脑、抓包、XSS 读取、日志级泄露）可离线解出密码并登录任意账号（含管理员），`secretkey` 更是直接给管理接口的凭据。`main.py:13-17` 的文档自己写着「不要使用默认值」，但代码是静默回落，不会报警。
- 建议：缺密钥时 **fail fast**（`raise RuntimeError`）或降级为「仅本标签页登录」（不写 Cookie），不要回落；Cookie 只存 `secrets.token_urlsafe(32)` 这类会话 id，服务端表存 `token → userid, expires_at, role`；无论何时都不要把密码写进 Cookie/session_state。若短期无法改造，至少把 `secretkey` 从 Cookie 中移除，并在启动时对默认密钥打 `st.warning`。

## P1

### P1-1 每次 rerun 至少 4 次会话/配置查询，其中 3 次发生在页面代码之前
- 位置：`main.py:164-176`、`pages/home.py:92`、`pages/home.py:738`
- 证据：
  ```python
  # main.py:166 / :176
  if not check_by_state():          # → api2.py:25 user.check → 一次 _user 查询
  userconfig = user.get_config(state["userid"]) or {}
  ```
  ```python
  # pages/home.py:92 与 :738（同一份配置再查两次）
  userconfig = user.get_config(state["userid"])
  current_cfg = user.get_config(state["userid"]) or {}
  ```
- 影响：每次交互（包括 P1-6 里每敲一个字触发的 rerun）都要多打 3 次 SQLite 查询，且 `get_config` 每次把整行转 dict。首页同一份配置被查了 3 遍（main + 两处 home）。
- 建议：`main.py:176` 取到后写入 `state["_cfg"] = userconfig`；`home.py:92`、`:738` 改读 `state["_cfg"]`。在登录、登出、修改资料/密码、改角色后显式 `state.pop("_cfg", None)`。`check_by_state()` 同理可缓存进 `state["_logged_in"]`，但注意其 docstring 明确要求实时性（api2.py:11-13），若保留实时查询，就让 UI 层复用上一次结果而不是每个卡片再问一次（见 P1-2）。

### P1-2 列表页/评论区逐条查登录态与点赞收藏态（N+1）
- 位置：`pages/home.py:241-250`（每篇文章卡片内部）、`pages/home.py:671`（每条评论）
- 证据：
  ```python
  # home.py:241, 249-250 —— 在 basic_information() 内部
  if check_by_state():
      liked = Like.is_liked(post["id"], state["userid"])
      bookmarked = Bookmark.is_bookmarked(post["id"], state["userid"])
  ```
  ```python
  # home.py:671 —— 在 for index, comment in enumerate(comments) 循环里
  if check_by_state():
  ```
- 影响：首页每页 5 篇文章 → 5 × 3 = 15 次额外查询（登录态、点赞、收藏各一次），详情页评论 n 条再加 n 次登录态查询；再叠加 P1-1 的 3 次，一次翻页/点赞/输入就产生 20+ 条串行 SQL。数据量与并发上来后首页明显变慢。
- 建议：`check_by_state()` 在页面顶层算一次并作为参数传进 `basic_information()`（或直接读 `state["_logged_in"]`）；点赞/收藏做批量前置查询——在渲染前用一次性 `Like.liked_ids(userid, post_ids)` / `Bookmark.bookmarked_ids(userid, post_ids)`（在 api 里新增 `post.in_([...])` 的实现），页面只查字典；评论循环把 `check_by_state()` 提到 `for` 之外。

### P1-3 列表页把每篇帖子的附件完整读进内存，却从未使用
- 位置：`pages/home.py:895-901`
- 证据：
  ```python
  files = {
      att.get("saved_name"): Attachment.get_file(att.get("saved_name"))
      for att in attachments
      if att.get("saved_name")
  }
  ```
  之后的 `home.py:903-908` 只有 `preview(att, col_a, saved_name, attpassword)`（缩略图走 `Attachment.get_thumbnail_*`）、`original_name`、`size`——`files` 再未被引用，函数到 `:909` 结束。
- 影响：每张列表卡片、每次 rerun 都把该文章所有附件的完整字节读入内存（可能是几十 MB 的视频），纯属浪费磁盘 IO 与内存，且随 rerun 反复发生；附件越多首页越慢。
- 建议：删除 `home.py:895-901`。若确实需要「文件是否存在」的判断，用 `os.path.exists(os.path.join(ATTACHMENTS_DIR, saved_name))`，或让 `preview()` 内部按需取缩略图（它已经这么做）。

### P1-4 头像链接用 f-string 拼 HTML，userid 未转义
- 位置：`pages/home.py:76-83`（调用点 `:239`、`:665`）
- 证据：
  ```python
  container.html(
      f'<a href="?user_id={userid}" title="查看个人主页" '
      f'style="display:inline-block;line-height:0;">'
  ```
  `userid` 来自注册输入，而 `User.register`（api.py:681-699）不校验字符集。
- 影响：注册 ID 为 `x"onmouseover="alert(1)` 之类即可跳出属性、破坏 `<a>` 结构；由于链接是拼接的，攻击者还能把 href 改写成外部 URL（本站头像处的钓鱼跳转），并在他人浏览的页面里注入任意 HTML 元素（样式覆盖、假登录框等）。注：`st.html` 会经 DOMPurify 清洗（详见「已排除」），所以不是直接 XSS，但「属性注入 + 内容冒充」已经足够定为 P1，且这段代码日后若被加进 `unsafe_allow_html=True` 的容器就会变成真 XSS。
- 建议：拼接前 `html.escape(userid, quote=True)`（或 `urllib.parse.quote(userid, safe="")`）；更好的做法是改用 `st.image(..., link=f"?user_id={quote(userid)}")` 或 `st.page_link("pages/home.py", query_params={"user_id": userid}, icon=...)`，由框架负责转义与客户端导航；同时在注册入口收紧 userid 到 `^[A-Za-z0-9_\-]{3,32}$`。

### P1-5 详情页附件全量读盘，每次 rerun 重新打 ZIP 并整包下发
- 位置：`pages/home.py:543-554`（读文件）、`pages/home.py:567-582`（ZIP）
- 证据：
  ```python
  # :550 files[saved_name] = Attachment.get_file(saved_name)   ← 循环里全量读
  # :568-572
  with ZipFile(zip_buffer, "w") as zf:
      for att in attachments:
          data = files.get(att.get("saved_name", ""))
          if data:
              zf.writestr(att.get("original_name", "download"), data)
  # :577 data=zip_buffer.getvalue()
  ```
- 影响：打开详情页后每一个 rerun（点赞、输入评论、切折叠、点预览）都会把全部附件重新读盘、在内存里重新压缩一遍，并把整个 ZIP 随页面 delta 下发；附件较大时页面卡顿、内存翻倍（读一次 + ZIP 一份）。`zf.writestr` 用的是未清洗的 `original_name`（api.py:1181 原样保存用户上传文件名），条目名含 `../` 时构成 Zip Slip，用户解压会写到目标目录之外。
- 建议：ZIP 只在用户真的点击下载时构建——把 `:567-582` 抽成 `@st.fragment` 里的「生成下载包」按钮（或用 `on_click` 回调），其余情况只渲染元信息；条目名用 `os.path.basename(original_name)` 并去重；大文件场景改用 `static/` 直链下载（`config.toml:3` 已 `enableStaticServing=true`），不要走 base64/内存流。

### P1-6 搜索/作者过滤每敲一个字符就整页 rerun
- 位置：`pages/home.py:752-765`（搜索）、`pages/home.py:767-779`（作者）
- 证据：
  ```python
  search_keyword = st.text_input(..., value=params.get("search_keyword", ""))
  if search_keyword != params.get("search_keyword", ""):
      if search_keyword:
          params["search_keyword"] = search_keyword
      else:
          del params["search_keyword"]
      st.rerun()
  ```
- 影响：输入「计算机」4 个字触发约 8 次完整 rerun，每次都要跑 P1-1/P1-2 的 20+ 条查询、所有帖子的缩略图与列表渲染，还要重建 Cookie 组件；移动端中文输入法组合输入（拼音未上屏）时抖动更明显，还会把搜索历史写进 URL 共享给他人（隐私层面小问题）。
- 建议：把筛选区放进 `st.form`（回车/点「搜索」才生效），或把输入 + 写 query_params 的逻辑放在 `@st.fragment` 里只重跑筛选区；至少去掉「`!=` 就立即 rerun」，改成输入框下加「搜索」按钮或 `on_change` 回调后写 params。

### P1-7 首页把 1.2MB 视频 base64 内嵌为背景
- 位置：`pages/home.py:708-715`（文件 `static/bg_embed.mp4` = 1,225,901 B）
- 证据：
  ```python
  render_video_background(
      Path(__file__).resolve().parent.parent / "static" / "bg_embed.mp4",
      embed=True, blur="6px", opacity=0.4, ...
  )
  ```
  `streamlit_video_background` 在 `embed=True` 时把本地文件读成 `data:video/mp4;base64,...`（其 `background.py:179-187`）。
- 影响：首页首屏 HTML 直接多出约 1.6MB（base64 膨胀 33%），移动端流量与首屏时间明显变差；背景视频未考虑 `prefers-reduced-motion`，对前庭敏感用户不友好；同一段视频在每次 rerun 时还会重新编码进 delta。
- 建议：改成 `embed=False` 的 URL 模式（`config.toml:3` 已开启 `enableStaticServing`，可用 `app/static/bg_embed.mp4` 直链），或换成 CSS 渐变/一张压缩后的 WebP；若保留视频，用媒体查询在 `prefers-reduced-motion: reduce` 下隐藏。

### P1-8 管理页每次 rerun 无条件全量取数，同一份数据取多次
- 位置：`pages/admin.py:41-51`、`:75-78`、`:299`、`:439`
- 证据：
  ```python
  # :44-45
  stats = get_attachment_stats()
  all_comments = get_comment_summary()
  # :76-78
  posts_all = Post.get_all()
  users_all = User.get_all()
  comments_all = get_comment_summary()
  # :299 又一次
  comment_summary = get_comment_summary()
  # :439
  all_attachments = get_all_attachments()
  ```
- 影响：一次管理页 rerun = 三次全量评论明细 + 全量文章表 + 全量用户表（`User.get_all()` 连 password/secret_key 一起查出来）+ 全量附件表；`expanded=False` 不会阻止内容执行，7 个 expander 每次都全跑。评论/用户上千后管理页打开要数秒，SQLite 读写锁也会拖慢前台。
- 建议：一个 rerun 内每份数据只取一次——把 `comments_all = get_comment_summary()` 提到顶部并传参给评论管理（删掉 `:299`），`get_attachment_stats()` 与 `get_all_attachments()` 合并成一次查询；给这些聚合加 `@st.cache_data(ttl="60s")`（后台 1 分钟陈旧可接受）；用 `st.fragment` 让每个 expander 独立重跑，避免一处交互整页重算。

### P1-9 「只读 SQL」把 `_user` 的 password/secret_key 明文渲染到页面
- 位置：`pages/admin.py:471-476`（与 `:152-155` 的声明矛盾）
- 证据：
  ```python
  query = col1.text_input("SQL 查询语句", placeholder="例如：SELECT * FROM _user")
  ...
  result = execute_sql(query)
  st.write(result)
  ```
  ```python
  # :152-155 同一页面的承诺
  # 不展示密码哈希与密钥等敏感列
  for col in ("password", "secret_key"): df = df.drop(columns=[col])
  ```
- 影响：占位符直接引导管理员 `SELECT * FROM _user`，结果原样打印——全站密码哈希与 `secret_key` 明文出现在浏览器里，极易进截图、录屏、浏览器缓存。`secret_key` 正是管理操作（改角色/删用户）的凭据（api.py:715-729），泄露等于交出提权口令。
- 建议：对结果做列级脱敏（列名命中 `password`/`secret_key`/`token` 一律替换成 `***`）后再渲染；或干脆禁止查询 `_user`（SQL 里出现 `_user` 就拒绝）；与 P0-2 的只读连接一并改造。

### P1-10 管理页备份/导出在每次 rerun 无条件生成整库与全表字节流
- 位置：`pages/admin.py:490-503`（备份）、`pages/admin.py:516-563`（6 个导出按钮）
- 证据：
  ```python
  # :490-498 —— 在 expander 里、按钮外，每次 rerun 都执行
  db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data.db")
  if os.path.exists(db_path):
      with open(db_path, "rb") as f:
          db_bytes = f.read()
      ...
      col_b.download_button(label="...", data=db_bytes, ...)
  ```
  ```python
  # :518 等 6 处：参数里直接调用
  data=export_users_csv(),
  ```
- 影响：每次进管理页/每次点任意按钮都把整个 `data.db`（含密码哈希）读进内存并随 delta 下发数 MB；6 份全表 CSV/JSON 每次重新序列化（`export_posts_json` 还带全部评论），tabs 中不可见的分支照样执行。库变大后管理页会越来越慢，最终 OOM。
- 建议：备份与导出都改成「先点生成、再下载」——把生成动作放进 `@st.fragment` 的按钮回调，`st.session_state` 持有生成的 bytes；备份用 SQLite 官方 `sqlite3.Connection.backup()`（或 `VACUUM INTO`）拿到一致性快照，而不是运行中直接读文件；导出结果按 `uploaded_at`/`ttl="5m"` 缓存。

### P1-11 评论 ID 输入直接 `int()`，非数字让整页抛异常
- 位置：`pages/admin.py:310-313`
- 证据：
  ```python
  del_cid = col_a.text_input("按评论 ID 删除", placeholder="输入评论 ID")
  if col_b.button("删除评论", type="primary", key="del_comment_by_id"):
      if del_cid:
          st.session_state["del_comment_confirm_id"] = int(del_cid)
  ```
- 影响：输入 `abc`、`12,3`、中文数字立刻 `ValueError`，Streamlit 显示红色 traceback——整个管理页（5 个图表 + 4 个 DataFrame + 备份 + 导出）白跑一遍，用户以为系统坏了；而同文件 `:239-240` 对文章 ID 就有 `except ValueError: st.error("文章 ID 必须是数字")`，两处风格不一致。
- 建议：改用 `st.number_input("评论 ID", min_value=1, step=1)`；若保留 text_input，则 `try: cid = int(del_cid) except ValueError: st.error("评论 ID 必须是数字")` 后在 else 分支继续。

### P1-12 收藏页固定 `page_size=100` 静默截断，且顺序与文档不符
- 位置：`pages/bookmarks.py:31`（文档声明在 `:5`）
- 证据：
  ```python
  # bookmarks.py:5（文件头）
  展示当前用户收藏的全部文章（按收藏顺序倒序为发布倒序）。登录后可见，
  # bookmarks.py:31
  posts = Post.get_filtered_paginate(post_ids=bookmarked_ids, page=1, page_size=100)
  ```
  `api.py:1013-1044` 的排序固定为 `created_at desc`（`sort_by` 只支持 date/views/likes/bookmarks），**不保留传入 `post_ids` 的顺序**；而 `Bookmark.get_bookmarked_post_ids`（api.py:1328-1336）是按收藏时间倒序返回的。
- 影响：收藏超过 100 条时第 101 条起永远不显示，页面也没有任何「仅显示前 100 条」的提示——用户会以为收藏丢了；顺序实际是「发布时间倒序」，与「按收藏顺序倒序」的承诺不符，找「刚收藏的文章」时行为反直觉。
- 建议：加分页或至少「共 N 条，显示前 100 条」的提示；要保留收藏顺序的话在页面侧按 `bookmarked_ids` 重排（先建 `{id: idx}` 映射再 `sorted`，避免 O(n²)），或给 api 增加 `sort_by="bookmark"` 支持。

### P1-13 发布流程附件先落盘、写库无异常处理 → 孤儿文件 + 裸 traceback
- 位置：`pages/publish.py:110-125`
- 证据：
  ```python
  if uploaded_files:
      for uploaded_file in uploaded_files:
          try:
              meta = Attachment.save(uploaded_file)   # 文件已写入 attachments/
              _attachments.append(meta)
          except Exception as e:
              st.error(f"附件 “{uploaded_file.name}” 保存失败：{e}")

  Post.publish(                                     # 无 try/except
      authorid=userid, title=new_title, content=...,
      attachments=_attachments, attpassword=attpassword, tags=selected_tags,
  )
  ```
- 影响：附件逐个写盘成功后，`Post.publish` 一旦抛异常（标题超长、`tags` 非法、SQLite 锁、磁盘满），用户看到整页 traceback 无法自助恢复，`attachments/` 里则留下永远不被引用的孤儿文件，只能靠管理页「孤立附件」手工清理。
- 建议：`Post.publish` 包 `try/except`，失败时删除刚写入的附件文件并 `st.error("发布失败，请重试")`；更稳的是让 api 提供「先写库、成功后落盘」或返回事务对象，页面负责补偿。

### P1-14 注册：密码不合规时点「注册」什么都不发生
- 位置：`pages/register.py:26-37`（校验）、`pages/register.py:47-50`（提交分支）
- 证据：
  ```python
  if password:
      if len(password) < 8:
          allowed = False
          st.error("密码长度不能小于8位！")
      if not any(char.isalpha() for char in password):
          allowed = False
          st.error("密码必须包含字母和数字的组合！")
  ...
  if password != password2:
      st.error("重新输入的密码与原来的密码不一致")
  elif not allowed:
      pass          # ← 静默失败
  ```
- 影响：`st.form` 内改密码后提交时，若不合规走 `elif not allowed: pass`——没有任何新提示，用户点了按钮、页面只是刷新一下，会以为按钮坏了；错误信息还可能停留在上一次输入的旧状态上。顺带：文案说「必须包含字母和数字」，代码只检查了「有字母」，纯字母长密码能通过。
- 建议：删掉 `:49-50` 的 `pass`，改成 `st.error("密码不符合要求：至少 8 位，且同时包含字母和数字")`；把规则抽成 `def _validate(pw) -> str | None` 同时用于 form 内提示与提交校验，并按文案补 `not any(c.isdigit() ...)` 检查。

### P1-15 图片生成结果常驻 session_state，无上限且每次全量渲染
- 位置：`pages/image_generate.py:41-44`、`:173-178`、`:186-194`
- 证据：
  ```python
  # :173-178
  st.session_state.image_results.append({"img_bytes": img_bytes, "ext": ...})
  # :186-194
  for idx, item in enumerate(st.session_state.image_results):
      st.image(item["img_bytes"], width="stretch")
      st.download_button(..., data=item["img_bytes"], key=f"download_{idx}")
  ```
- 影响：一次 10 张 3840×2160 的图就可能几十 MB，全部常驻该 session 且永不清理、没有「清空」按钮；每次 rerun 都重新 base64 编码全部图片并下发，长期使用页面越来越卡，多用户并发时直接把进程内存打满。
- 建议：限制保留数量（如最近 6 张，超出丢弃最旧并 `st.toast` 提示）；加「清空结果」按钮；预览收进 `st.expander` 或 `@st.fragment` 内按需渲染；长期方案是把图片写入 `static/generated/`，session 里只存路径/URL（配合 `enableStaticServing`）。

### P1-16 图片生成假设返回数量与请求一致，且下载无超时/大小限制
- 位置：`pages/image_generate.py:163-169`、`:181-182`
- 证据：
  ```python
  for i in range(number):
      data = result.data[i]              # 少返回一张 → IndexError
      if data.b64_json: img_bytes = base64.b64decode(data.b64_json)
      elif data.url:
          with urllib.request.urlopen(data.url) as resp:   # 无 timeout
              img_bytes = resp.read()                      # 无大小上限
  ...
  except Exception as e:
      st.error(f"生成失败：{e}", icon=":material/error:")
  ```
- 影响：上游少返回（或返回空列表）时 `IndexError` 被宽泛 `except` 捕获，页面显示「生成失败」——已成功生成且已计费的图片全部丢失（不会进入 `image_results`）。`urlopen` 无超时：上游 `data.url` 响应慢会一直占住 worker，`resp.read()` 无上限则可能把超大文件读进内存。异常信息还会把底层错误（含 base_url/请求细节）直接展示给用户（P2 级）。
- 建议：改为 `for data in result.data:` 按实际返回遍历，并提示「请求 N 张，实得 M 张」；`urlopen(data.url, timeout=60)` + `resp.read(MAX_BYTES + 1)` 校验大小；把单张解析包进自己的 try，只失败那一张不影响其余；对外错误统一成「生成失败，请稍后重试（请求 ID xxx）」，细节写日志。

### P1-17 config.toml 把所有语义色改成灰阶，错误/成功/警告失去颜色
- 位置：`.streamlit/config.toml:31-37`（浅色）、`.streamlit/config.toml:75-81`（深色）、`:9-11`（图表色板）
- 证据：
  ```toml
  # :31-37（深色 :75-81 同样全部灰阶）
  redColor = "#111111"
  orangeColor = "#333333"
  yellowColor = "#555555"
  greenColor = "#777777"
  blueColor = "#999999"
  # :9-11
  chartCategoricalColors = ["#111111", "#333333", ... "#888888"]
  ```
- 影响：`st.error` / `st.success` / `st.warning` / `st.info` 与所有图表都退化为黑白灰，用户无法靠颜色区分「失败」与「成功」（发布、删除、登录、注册流程里这两类提示都是高频的）；深色主题下 `redColor="#F5F5F5"` 更接近正文色，可访问性明显倒退。
- 建议：恢复语义色（例如 error `#C0392B`、success `#2E7D32`、warning `#E67E22`、info `#1565C0`），图表色板至少保留 2–3 个可区分色；若坚持黑白极简风，则给关键提示补 icon 与文案前缀（`st.error(..., icon=":material/error:")`）以保证不依赖颜色也能读。

### P1-18 深色主题靠 `data-testid` CSS hack，且首轮 `st.context.theme` 不可信
- 位置：`main.py:81-122`（深色 CSS）、`main.py:125-149`（移动端 CSS）
- 证据：
  ```python
  if st.context.theme.type == "dark":
      st.html("""
          button[data-testid="stBaseButton-primary"],
          button[kind="primary"] { color: #000000 !important; }
          .stMultiSelect [data-tag] { ... }
          [data-testid="stPills"] button[aria-checked="true"] { ... }
      """)
  ```
  移动端同样依赖 `[data-testid="stColumns"]`、`[data-testid="stColumn"]`、`[data-testid="stVerticalBlock"]`。
- 影响：这些 `data-testid` 是 Streamlit 前端内部实现，随小版本修改即可失效（失效后深色模式主按钮又回到白底白字不可见）；`.stMultiSelect` 这类 class 名同样不稳定。另外 Streamlit 文档明确 `st.context.theme.type` 在「首轮加载」和「切换主题的那一次 rerun」可能返回错误值，深色用户首次打开页面时可能取不到这段 CSS。
- 建议：优先从根上解决——`[theme.dark]` 里把 `primaryColor` 改成自带黑字的浅色（如 `#E5E5E5`），就不需要这段 hack；必须保留时把所有选择器集中到 `main.py` 顶部的一个 `_DARK_FIX_CSS` 常量并注释「升级 Streamlit 后需回归验证的 testid 清单」，同时把 `st.context.theme.type` 的判断降级为兜底（用 `@media (prefers-color-scheme: dark)` 同时覆盖）。

## P2

### P2-1 favicon 指向不存在的文件
- 位置：`main.py:44`
- 证据：`st.set_page_config("FreeLake · 自由论坛", page_icon="logo.ico", layout="centered")`；项目根目录没有 `logo.ico`，真实文件在 `pages/logo.ico`（16,958 B）。
- 影响：`st.set_page_config` 的 favicon 解析（`streamlit/commands/page_config.py:65-103`）在文件不存在时会吞掉异常并把字符串原样当图标 → 浏览器标签页显示默认/裂图图标，控制台多一条无害报错。
- 建议：`page_icon=str(Path(__file__).parent / "pages" / "logo.ico")`（或把文件移到根目录），也可直接用 emoji `":material/forum:"`。

### P2-2 `st.cache` 兼容垫片是死代码，且改写了第三方库语义
- 位置：`main.py:24-27`
- 证据：
  ```python
  # streamlit_cookies_manager 仍在使用已移除的 st.cache：
  # 新版 Streamlit（≥1.49 已删除）下先补一个兼容别名，避免导入即报错
  if not hasattr(st, "cache"):
      st.cache = st.cache_data
  ```
  实测 1.61.1 里 `st.cache` 仍存在（只是 deprecation 警告），垫片分支从不执行。
- 影响：注释与事实不符，误导后续维护；一旦某天真删掉 `st.cache`，别名会让 `streamlit_cookies_manager` 的 `@st.cache(...)` / `allow_output_mutation` 之类参数落到 `st.cache_data` 上，参数不被识别 → 导入即 `TypeError`，故障从「警告」升级为「整站打不开」。
- 建议：删掉垫片，改为升级/替换 `streamlit-cookies-manager`（或本地 fork 掉其中的 `st.cache` 用法），并在 `requirements.txt` 里 pin `streamlit` 版本，附一条「升级前先确认第三方库不再用 st.cache」的注释。

### P2-3 Cookie 未就绪时先按未登录渲染，首屏会闪出「登录/注册」
- 位置：`main.py:63-72`、`main.py:166`
- 证据：
  ```python
  if cookies.ready():
      state["userid"] = cookies.get("userid", "")
      state["password"] = cookies.get("password", "")
  else:
      st.info("⏳ 正在加载 Cookies，请稍候...")
  ...
  if not check_by_state():   # 首轮必为未登录
      account_pages.append(login_page); account_pages.append(register_page)
  ```
- 影响：已登录用户刷新首页时，顶部导航先出现「登录/注册」并伴随一条 info 横幅，Cookie 组件回传后的下一次 rerun 才切换成「发布/收藏/退出」——两帧错误状态，看起来像掉登录（`main.py:63-66` 的注释解释了为什么不用 `st.stop()`，理由成立，但视觉问题仍在）。
- 建议：未就绪时用占位骨架替代真实导航（`placeholder = st.empty()`，ready 后再写导航；或 `if not cookies.ready(): st.empty()` + 稍后 `st.rerun()`），避免渲染出错误的登录态。

### P2-4 `st.switch_page` 与 `st.page_link` 用法自相矛盾
- 位置：`pages/home.py:471`、`pages/logout.py:21`、`pages/publish.py:132`（使用）；`pages/home.py:721-723`、`pages/bookmarks.py:8-9`（声称不要用）
- 证据：
  ```python
  # home.py:721-723
  # 全部使用 st.page_link（客户端导航）：与服务端导航（switch_page）
  # 不同，不会触发 Cookie 组件重载导致卡在「正在加载 Cookies」；
  ```
  ```python
  # home.py:471 / logout.py:21 / publish.py:132
  st.switch_page("pages/logout.py")
  st.switch_page("pages/home.py")
  ```
- 影响：代码与注释互相否定，后续维护者无法判断哪种做法正确；`publish.py:132` 在发布成功后走服务端跳转，慢网络下用户可能先看到「正在加载 Cookies」横幅再看到首页。
- 建议：统一策略并写进注释——纯导航（返回主页、去登录、去收藏）一律 `st.page_link`；只保留必须服务端跳转的三处（退出登录、发布成功、修改密码后重登），集中在 `home.py:721-723` 的注释里列出清单并说明原因。

### P2-5 默认日期起点 2026-08-01，让更早的文章默认不可见
- 位置：`pages/home.py:41`、`:798-812`
- 证据：
  ```python
  DEFAULT_DATE_START = datetime.date(2026, 8, 1)  # 发布日期起点
  ...
  date_sel = st.date_input(..., value=(DEFAULT_DATE_START, today), key="home_date_range")
  if date_sel and len(date_sel) == 2:
      date_start, date_end = date_sel[0], date_sel[1]
  else:
      date_start, date_end = DEFAULT_DATE_START, today   # 清空也回落到默认
  ```
- 影响：2026-08-01 之前发布的文章在首页默认查不到，用户只能靠手动改日期范围；页面没有醒目的「已按日期过滤」提示（只有「始终生效」的注释），看起来像文章丢失。
- 建议：默认改为 `None`（不加日期条件）或默认值为最早文章日期；如果确实要默认最近区间，在筛选区标题旁用 `st.caption` 显示当前生效区间，并加「清除日期」按钮。

### P2-6 分页大小 5 在两处硬编码
- 位置：`pages/home.py:827`、`pages/home.py:842`
- 证据：
  ```python
  page = st.pagination(num_pages=max((total + 5 - 1) // 5, 1), ...)
  ...
  posts = Post.get_filtered_paginate(..., page=page, page_size=5, ...)
  ```
- 影响：哪天调整页大小只改一处，就会出现多算/少算页数（末页空页或文章被跳过），且不易发现。
- 建议：提取 `PAGE_SIZE = 5`（或放进 `const.py`），两处统一引用；页数计算用 `math.ceil(total / PAGE_SIZE)` 更直白。

### P2-7 `publish_tags` 同时给 default 又被程序写入 session_state
- 位置：`pages/publish.py:74`、`:76`、`:42-44`
- 证据：
  ```python
  selected_tags = st.multiselect("选择标签", tags, default=[tags[0]],
                                 accept_new_options=False, key="publish_tags")
  # :42-44 草稿加载
  st.session_state["publish_tags"] = [t for t in (d.get("tags") or []) if t in tags]
  ```
- 影响：Streamlit 会打警告「widget 带 default 又被 Session State 赋值」；从草稿加载后 `default=[tags[0]]` 仍可能覆盖草稿标签，用户发现草稿标签丢了。
- 建议：去掉 `default`，首次进入用 `st.session_state.setdefault("publish_tags", [])`；草稿加载保持 `state["publish_tags"] = ...` + `st.rerun()`（现有逻辑即可）。

### P2-8 草稿删除无二次确认；附件上传无类型/大小限制
- 位置：`pages/publish.py:48-50`、`pages/publish.py:88-92`
- 证据：
  ```python
  if c3.button("删除", key=f"del_draft_{d['id']}"):
      Draft.delete(userid, d["id"])      # 与「继续编辑」并排，无确认
      st.rerun()
  ...
  uploaded_files = st.file_uploader("上传附件（可选）", accept_multiple_files=True)
  ```
  `Attachment.save`（api.py:1168）用 `uploaded_file.getvalue()` 一次性读全量；Streamlit 默认单文件上限 200MB。
- 影响：误点「删除」立刻丢草稿；上传大文件时整个 worker 内存翻倍（原始 + 写盘），多用户并发上传大视频容易 OOM。
- 建议：草稿删除改成两步（第一次点变成「确认删除？」）；uploader 加 `type=[...]` 白名单并在页面侧提示大小上限（`if f.size > 20 * 1024 * 1024: st.error(...)`），同时在 `[server] maxUploadSize` 里显式设置。

### P2-9 管理页孤立文件 `getsize` 无保护；文件浏览器有调试残留
- 位置：`pages/admin.py:459-462`、`:569-578`
- 证据：
  ```python
  for f in orphans:
      fp = os.path.join(ATTACHMENTS_DIR, f)
      size = os.path.getsize(fp)     # 无 try，文件刚被删即 FileNotFoundError
      st.text(f"{f} ({format_size(size)})")
  ...
  event = st_file_browser(ATTACHMENTS_DIR, key="deep", show_choose_file=True,
                          show_delete_file=True, show_new_folder=True,
                          show_upload_file=True)
  st.write(event)                     # 调试残留
  ```
- 影响：孤立文件在列出后到渲染之间被删除/移动（并发清理）时整页崩；`st.write(event)` 把组件返回的原始字典（含绝对路径）直接印在页面上；`show_delete_file=True` 一键删文件没有确认；通过浏览器上传的文件绕过 `Attachment.save`，只落盘不登记元数据，直接制造新的孤儿文件。
- 建议：`try: size = os.path.getsize(fp) except OSError: continue`；删掉 `st.write(event)`；上传统一走 `st.file_uploader` + `Attachment.save()`；文件浏览器设为只读（`show_delete_file=False`、`show_upload_file=False`），删除操作改为带确认的按钮。

## 已排除 / 非问题

1. **`st.html` 的注入不是当页 XSS**：Streamlit 1.61 的 `st.html` 走 DOMPurify 清洗（`streamlit/elements/html.py:56-57`，且 `unsafe_allow_javascript=False` 默认），事件属性与 `javascript:` URL 会被剥离；全仓也没有 `unsafe_allow_html=True`。因此 `home.py:76-83` 按 P1-4 定为「属性注入 / DOM 破坏 / 钓鱼跳转」而不是直接 XSS，后续 agent 不必再升级该条。
2. **缩略图已有缓存**：`Attachment.get_thumbnail_bytes` 带 `@st.cache_data(ttl="10m", max_entries=256)`（api.py:1188-1189），`home.py:321` 的 `get_thumbnail_base64` 命中缓存后只做一次 base64 编码，不是「每次 rerun 重新用 Pillow 解码」。真正未缓存的是 `get_avatar_bytes`（api.py:1559-1574，每次读盘），但头像文件小、每页读盘次数约 10 次，收益低于引入缓存失效的复杂度，故未单列。
3. **`execute_sql` 不存在多语句堆叠**：peewee 的 `execute_sql` 走 `sqlite3.Cursor.execute`，多语句会直接被拒（"You can only execute one statement at a time"），所以 P0-2 的真实风险是 `WITH ... DELETE/UPDATE` 与 `PRAGMA`，不是分号拼接——避免同类复查时走错方向。
4. **导出接口本身不泄露凭据**：`export_users_csv/json`（api.py:1581-1700）已剔除 `password`/`secret_key`，P1-10 的问题是无条件生成与下发，不是内容泄露；页面上的泄露点只有 SQL 工具（P1-9）。
5. **首页「最新一条评论」与评论作者已批量查询**：`home.py:852-853` 的 `User.get_configs(...)` 与 `Post.get_last_comments(posts)` 都是批量接口，这里没有 N+1；N+1 只出现在 `basic_information()` 内部（P1-2）。
