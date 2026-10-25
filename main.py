"""
FreeLake 入口（main.py）
========================

Streamlit 论坛应用的主入口脚本，负责：

1. 初始化会话状态（userid/password）与加密 Cookie 管理器；
2. 根据登录状态构建顶部导航（st.navigation）；
3. 按角色（普通用户 / 管理员）展示不同的「账号」页面组。

运行方式：``streamlit run main.py``

安全约定：
- Cookie 加密密钥必须来自环境变量 ``COOKIES_PASSWORD`` 或
  ``.streamlit/secrets.toml`` 的 ``[cookies]`` 段。**不再回落公开常量**：
  未配置时使用进程级随机密钥并显示醒目警告（漏洞关闭，代价是重启后
  所有人需重新登录）；
- 登录状态以「userid + 明文密码」形式保存在加密 Cookie 中，由
  ``api2.check_by_state`` 每次直接查库校验。
"""

import os
import secrets

import streamlit as st

# streamlit_cookies_manager 仍在使用已移除的 st.cache：
# 新版 Streamlit（≥1.49 已删除）下先补一个兼容别名，避免导入即报错
if not hasattr(st, "cache"):
    st.cache = st.cache_data

from streamlit_cookies_manager import EncryptedCookieManager

from api import User
from api2 import check_by_state
from const import admin


def _secrets_get(key, default=None):
    """安全读取 st.secrets，secrets 文件缺失时返回默认值"""
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


st.set_page_config("FreeLake · 自由论坛", page_icon="logo.ico", layout="centered")

# ---- 会话状态初始化 ----
state = st.session_state

if "userid" not in state:
    state["userid"] = ""
if "password" not in state:
    state["password"] = ""
if "secretkey" not in state:
    # 管理员操作（页面上「用户管理」）会用 state["secretkey"] 调 check_admin；
    # 缺这个键会在刷新后直接 KeyError，所以即使未登录也要先置空。
    state["secretkey"] = ""

# ---- 加密 Cookie：持久化登录状态（浏览器端加密存储） ----
# 安全：绝不使用硬编码/公开的兜底密钥（等同于把解密密钥写进仓库）。
# 未配置时退化为「进程级随机密钥」——攻击者不可知，因此漏洞关闭；
# 代价是服务重启（或多进程部署）后所有人需要重新登录，并会看到醒目提示。
_cookie_password = os.environ.get("COOKIES_PASSWORD") or _secrets_get(
    "cookies", {}
).get("password")
if not _cookie_password:
    _cookie_password = secrets.token_urlsafe(32)
    st.warning(
        "未配置 Cookie 加密密钥（环境变量 `COOKIES_PASSWORD` 或 secrets 的 "
        "`[cookies] password`）。本次进程已生成**临时随机密钥**："
        "服务重启或多进程部署后所有用户都需要重新登录，请尽快配置。",
        icon=":material/key_off:",
    )
cookies = EncryptedCookieManager(
    prefix="freelake/",
    password=_cookie_password,
)
state["cookies"] = cookies

# Cookie 组件首轮渲染需要浏览器回传。注意：未就绪时**不要** st.stop()——
# 服务端导航（如发布后的 st.switch_page）会让 Cookie 组件重载；若在此处停止
# 执行，页面会死锁在「正在加载 Cookies」且需手动刷新。改为未就绪时先按当前
# 会话状态继续渲染（顶部提示加载中），组件值回传后的下一次 rerun 自动纠正登录态。
if cookies.ready():
    # 从 Cookie 恢复登录状态（未登录时为空字符串）
    state["userid"] = cookies.get("userid", "")
    state["password"] = cookies.get("password", "")
    # secretkey 必须一起恢复：页面刷新后 session_state 会清空，而
    # pages/admin.py 的「修改角色 / 删除用户」用 state["secretkey"] 调 check_admin。
    state["secretkey"] = cookies.get("secretkey", "")
else:
    st.info("⏳ 正在加载 Cookies，请稍候...")

# ---- 深色模式下 primary 控件文字改为黑色 ----
# config.toml 没有控件文字色选项；深色主题 primaryColor 为白色（#FFFFFF），而 Streamlit
# 默认在 primaryColor 上渲染白字（白底白字不可见）。这里仅在深色主题下注入 CSS，
# 把主按钮 / 多选标签 / 已选 chips 的文字改为黑色。
# 覆盖：st.button、st.download_button（二者内部均渲染 stBaseButton-primary）、
# st.form_submit_button（stBaseButton-primaryFormSubmit）、st.multiselect 已选标签（data-tag）、
# st.pills / st.segmented_control 已选项。
if st.context.theme.type == "dark":
    st.html(
        """
        <style>
        /* 主按钮：st.button / st.download_button */
        button[data-testid="stBaseButton-primary"],
        button[kind="primary"] {
            color: #000000 !important;
        }
        button[data-testid="stBaseButton-primary"]:hover,
        button[data-testid="stBaseButton-primary"]:focus,
        button[data-testid="stBaseButton-primary"]:active,
        button[data-testid="stBaseButton-primary"]:disabled,
        button[kind="primary"]:hover,
        button[kind="primary"]:focus,
        button[kind="primary"]:active,
        button[kind="primary"]:disabled {
            color: #000000 !important;
        }
        /* 表单主提交按钮：st.form_submit_button */
        button[data-testid="stBaseButton-primaryFormSubmit"],
        button[data-testid="stBaseButton-primaryFormSubmit"]:hover,
        button[data-testid="stBaseButton-primaryFormSubmit"]:focus,
        button[data-testid="stBaseButton-primaryFormSubmit"]:active,
        button[data-testid="stBaseButton-primaryFormSubmit"]:disabled {
            color: #000000 !important;
        }
        /* 多选下拉已选标签：st.multiselect */
        .stMultiSelect [data-tag],
        .stMultiSelect [data-tag]:focus-visible {
            color: #000000 !important;
        }
        /* 已选 chips：st.pills / st.segmented_control */
        [data-testid="stPills"] button[aria-checked="true"],
        [data-testid="stSegmentedControl"] button[aria-checked="true"],
        [data-testid="stPills"] button[aria-checked="true"]:hover,
        [data-testid="stSegmentedControl"] button[aria-checked="true"]:hover {
            color: #000000 !important;
        }
        </style>
        """
    )

# ---- 移动端适配（窄屏下收紧留白与字号，允许栅格换行） ----
st.html(
    """
    <style>
    @media (max-width: 600px) {
        .block-container {
            padding-top: 1rem;
            padding-bottom: 1rem;
            padding-left: 0.9rem;
            padding-right: 0.9rem;
        }
        [data-testid="stColumns"] {
            flex-wrap: wrap;
            gap: 0.6rem;
        }
        [data-testid="stColumn"] {
            min-width: 45% !important;
        }
        h1 { font-size: 1.55rem !important; }
        h2 { font-size: 1.25rem !important; }
        h3 { font-size: 1.1rem !important; }
        [data-testid="stVerticalBlock"] { gap: 0.6rem; }
    }
    </style>
    """
)

# ---- 页面注册（st.navigation 多页应用） ----
home_page = st.Page("pages/home.py", title=":material/home: 首页")
publish_page = st.Page("pages/publish.py", title=":material/publish: 发布文章")
bookmarks_page = st.Page("pages/bookmarks.py", title=":material/bookmark: 我的收藏")
login_page = st.Page("pages/login.py", title=":material/login: 登录")
register_page = st.Page("pages/register.py", title=":material/person: 注册")
logout_page = st.Page("pages/logout.py", title=":material/logout: 退出登录")
admin_page = st.Page("pages/admin.py", title=":material/security: 管理员页面")
image_generate_page = st.Page(
    "pages/image_generate.py", title=":material/auto_awesome: 图片生成"
)

# ---- 按登录状态与角色组装「账号」导航组 ----
user = User()
account_pages = []
if not check_by_state():
    # 未登录：只显示 登录 / 注册
    account_pages.append(login_page)
    account_pages.append(register_page)
else:
    # 已登录：发布文章 / 我的收藏 / 退出登录；
    # 管理员额外获得管理页面与图片生成。
    # 个人资料与设置已并入主页「?user_id=本人」视图（调用处跳转至此）。
    account_pages.append(publish_page)
    account_pages.append(bookmarks_page)
    userconfig = user.get_config(state["userid"]) or {}
    if userconfig.get("role") == admin:  # NOQA
        account_pages.append(admin_page)
        account_pages.append(image_generate_page)
    account_pages.append(logout_page)


st.title("FreeLake")


nav = st.navigation(
    {
        ":material/home: 首页": [home_page],
        ":material/manage_accounts: 账号": account_pages,
    },
    position="top",
)
nav.run()

with st.bottom:
    st.caption("© 2026 XiangQinxi · FreeLake 自由论坛")
