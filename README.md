# FreeLake · 自由论坛

基于 **Streamlit** 构建的轻量论坛程序，包含完整的用户体系、文章发布、评论、点赞、收藏、附件上传等基础功能，使用 **peewee + SQLite** 快速构建数据层。

在线体验：https://freelake.streamlit.app/

---

## 功能特性

- **用户体系**：注册 / 登录 / 退出、个人资料（昵称、自我介绍、头像）、密码修改
- **文章**：发布、编辑、删除、搜索、标签筛选、分页浏览、查看个人主页、浏览量统计
- **搜索**：关键词 + 作者 + 标签 + 日期范围组合筛选，关键词高亮展示
- **评论**：文章评论区（最新一条评论展示在首页列表）、评论编辑/删除、Markdown 排版、楼层编号、@用户名 提及
- **互动**：点赞、收藏、「我的收藏」快捷入口
- **举报**：文章 / 评论举报，管理后台受理（处理 / 驳回 / 删除目标）
- **草稿**：未完成内容保存为草稿，从「草稿箱」继续编辑
- **个人主页**：头像与简介横幅 + 发布 / 浏览 / 获赞 / 获藏统计
- **附件**：多文件上传、图片在线预览（缩略图缓存）、音频/视频预览、ZIP 打包下载、附件专属密码保护
- **管理后台**（管理员专属）：用户 / 文章 / 评论 / 附件 / 举报管理、孤立数据清理、数据导出（CSV/JSON）、文件浏览
- **AI 图片生成**（管理员专属）：基于 OpenAI 兼容接口的文生图 / 图生图
- **主题与移动端**：亮 / 暗主题可切换（暗色模式适配完善），移动端响应式适配
- **安全**：密码 PBKDF2 加盐哈希（旧 sha256 自动升级）、管理员凭据不落仓库、Cookie 加密存储登录态

## 技术栈

| 组件 | 说明 |
| --- | --- |
| [Streamlit](https://streamlit.io/) | 应用框架（st.navigation 多页应用） |
| [peewee](http://docs.peewee-orm.com/) | ORM，SQLite 数据库（`data.db`） |
| Pillow | 头像裁剪与附件缩略图 |
| streamlit-cookies-manager | 加密 Cookie 持久化登录状态 |
| openai | AI 图片生成接口 |

## 快速开始

### 本地部署

```bash
# 1. 克隆仓库
git clone https://github.com/XiangQinxi/freelake.git
cd freelake

# 2. 安装依赖
pip install -r requirements.txt

# 3. 创建密钥文件 .streamlit/secrets.toml（参考下方「密钥配置」）

# 4. 启动
streamlit run main.py
```

### 部署到 Streamlit Cloud

把 `[admin]`、`[cookies]` 等配置填入云端后台的 **Secrets** 即可，不要提交到仓库。

## 密钥配置（.streamlit/secrets.toml）

该文件已被 `.gitignore` 忽略，**首次运行必须创建**：

```toml
# 管理员账号（首次启动时用于创建管理员，请及时修改密码）
[admin]
userid = "xqx"
username = "XiangQinxi"
password = "你的管理员密码"

# Cookie 加密密钥（任意足够长的随机字符串；修改后所有用户需重新登录）
[cookies]
password = "一串足够长的随机字符串"

# AI 图片生成（可选，管理员页面「图片生成」功能）
[ai]
base_url = "https://api.openai.com/v1"
api_key = "sk-..."
```

管理员凭据的读取优先级：`st.secrets` > 环境变量（`ADMIN_USERID` / `ADMIN_USERNAME` / `ADMIN_PASSWORD`）> `config.toml`（仅兜底，不应包含真实密码）。

## 项目结构

```
freelake/
├── main.py                  # 入口：会话/Cookie 初始化 + st.navigation 导航
├── api.py                   # 数据层：peewee 模型 + 业务接口 + 密码哈希 + 导出/管理工具
├── api2.py                  # 会话辅助层：登录状态判断（依赖 st.session_state）
├── const.py                 # 全局常量：文章标签、角色标识
├── config.toml              # 管理员基础信息（密码放在 secrets 中，勿提交）
├── requirements.txt         # Python 依赖
├── data.db                  # SQLite 数据库（gitignore）
├── attachments/             # 上传的附件文件（gitignore）
├── avatars/                 # 用户头像文件（gitignore）
├── pages/                   # 页面目录（st.navigation 引用）
│   ├── home.py              # 首页：列表/搜索/详情/评论/用户资料与账号设置（?user_id=本人）
│   ├── publish.py           # 发布文章（含草稿箱）
│   ├── login.py             # 登录
│   ├── register.py          # 注册
│   ├── logout.py            # 退出登录
│   ├── admin.py             # 管理员后台
│   └── image_generate.py    # AI 图片生成（管理员）
└── v1/                      # 上代版本文件，勿动
```

### 数据层接口（api.py）

| 类 / 函数 | 职责 |
| --- | --- |
| `User` | 注册、登录（含密码自动升级）、资料查询/修改、删除、批量查询 `get_configs` |
| `Post` | 发布、编辑、删除、搜索分页、详情（评论批量加载）、评论增删、最新评论批量 `get_last_comments`、组合筛选（关键词/标签/作者/日期）、`get_author_stats` 作者统计 |
| `Report` | 举报提交（防重复）、查询、受理处理（处理 / 驳回） |
| `Draft` | 草稿保存、更新、查询、删除 |
| `Attachment` | 附件保存/读取、缩略图（`st.cache_data` 缓存 10 分钟） |
| `Like` / `Bookmark` | 点赞、收藏（切换 / 查询 / 计数） |
| `save_avatar` / `get_avatar_bytes` | 头像裁剪保存（相对路径）/ 读取（兼容旧绝对路径） |
| `hash_password` / `verify_password` | PBKDF2 加盐哈希与校验（兼容旧 sha256） |
| `admin_credentials` | 按 secrets > 环境变量 > config.toml 获取管理员凭据 |
| `export_*` / `get_*` / `delete_*` | 数据导出与管理员清理工具 |

### 数据模型

| 表 | 字段要点 |
| --- | --- |
| `_user` | userid, username, password(哈希), description, avatar, role, secret_key |
| `_post` | id(自增), authorid, title, content, created_at, attachments(JSON), attpassword, tags(JSON), comments(JSON), views |
| `_comment` | id(自增), postid, userid, content, created_at |
| `_like` / `_bookmark` | postid, userid |
| `_report` | postid(可空), commentid(可空), target_userid, reporter_userid, content_preview, reason, status(pending/handled/dismissed), handled_by, handled_at, note |
| `_draft` | userid, title, content, tags(JSON), attpassword |

## 安全说明

- **密码**：数据库仅存哈希。新用户使用 PBKDF2-SHA256（20 万次迭代、随机盐）；旧版 sha256 密码在登录校验通过后自动升级。
- **管理员凭据**：不提交到仓库，从 `.streamlit/secrets.toml` 或环境变量读取。
- **登录状态**：`userid`/`password` 以加密形式存入浏览器 Cookie（密钥来自 secrets `[cookies]`），由 `api2.check_by_state` 每次直接查库校验。
- **敏感展示**：管理后台的数据表格已剔除 `password`/`secret_key` 列；文件浏览器仅指向 `attachments/` 目录。

## 常见问题

**登录后仍显示未登录？**
换用新的 Cookie 加密密钥后，旧浏览器 Cookie 无法解密，表现为「未登录」。**重新登录一次即可**，登录成功会写入新密钥加密的 Cookie。

**管理员账号密码忘了？**
在 `.streamlit/secrets.toml` 的 `[admin]` 段修改密码后删除 `data.db` 重新启动即可重新引导创建（注意会清空所有数据）；或在「用户配置 → 修改密码」中自助重置。

**上传的图片在首页很卡？**
附件缩略图已带 10 分钟缓存，重新发布新附件后最多延迟 10 分钟刷新。

## 计划

- [x] 首页文章展示最新一条评论
- [x] 完善举报功能（文章 / 评论举报 + 后台受理）
- [x] 评论 Markdown 排版、楼层编号、@提及
- [x] 搜索升级（关键词高亮 + 作者筛选）
- [x] 用户主页数据统计
- [x] 草稿箱与继续编辑
- [x] 暗色模式适配完善 + 移动端适配

## 许可证

© 2026 XiangQinxi · All rights reserved
