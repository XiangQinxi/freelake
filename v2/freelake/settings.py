"""
配置与凭据（freelake/settings.py）
=================================

`Config` 读写项目根目录的 `config.toml`；`admin_credentials()` 按
「secrets → 环境变量 → config.toml」的优先级取引导管理员凭据。
密码不应该出现在 config.toml 里（本地开发放 .streamlit/secrets.toml）。
"""

import os

import streamlit as st
import toml

class Config:
    config = {}

    def __init__(self):
        self.load()

    def load(self):
        with open("config.toml", "r+", encoding="utf-8") as f:
            self.config = toml.load(f)

    def save(self):
        with open("config.toml", "w+", encoding="utf-8") as f:
            toml.dump(self.config, f)


def admin_credentials() -> dict:
    """按优先级获取管理员凭据：st.secrets > 环境变量 > config.toml。

    密码不应提交到仓库，本地开发请写入 .streamlit/secrets.toml（已 gitignore）。
    """
    # 1. st.secrets（本地 .streamlit/secrets.toml 或 Streamlit Cloud 后台）
    try:
        for section in ("admin", "default_admin"):
            item = st.secrets.get(section, {}) or {}
            if item.get("password"):
                return {
                    "userid": item.get("userid") or "admin",
                    "username": item.get("username") or item.get("userid") or "Admin",
                    "password": item["password"],
                }
    except Exception:
        pass
    # 2. 环境变量
    env = {
        "userid": os.environ.get("ADMIN_USERID"),
        "username": os.environ.get("ADMIN_USERNAME"),
        "password": os.environ.get("ADMIN_PASSWORD"),
    }
    if env.get("password"):
        env["username"] = env["username"] or env["userid"] or "Admin"
        return env
    # 3. config.toml（仅作本地兜底，不应包含真实密码）
    return Config().config.get("admin", {}) or {}
