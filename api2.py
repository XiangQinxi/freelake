"""
FreeLake 会话辅助层（api2.py）
==============================

依赖 Streamlit 会话状态（st.session_state）的轻量封装，供入口与各页面
共享登录状态判断。

注意：
- 本模块的 ``state`` 在导入时绑定 ``st.session_state``，Streamlit 的会话
  代理会在每次访问时解析当前会话，因此跨 rerun 使用是安全的。
- ``check_by_state`` 直接查库校验（每次调用一次索引查询），不要在此做
  会话内记忆化缓存——曾有记忆化把正确凭据缓存为 False 导致登录失败的教训。
"""
import streamlit as st

from api import User

user = User()
state = st.session_state


def check_by_state() -> bool:
    """检查会话状态：当前会话是否处于已登录状态。"""
    return user.check(state.get("userid", ""), state.get("password", ""))
