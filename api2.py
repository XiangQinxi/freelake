import streamlit as st

from api import User

user = User()
state = st.session_state


def check_by_state() -> bool:
    """检查会话状态"""
    return user.check(state["userid"], state["password"])
