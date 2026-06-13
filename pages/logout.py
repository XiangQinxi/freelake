import streamlit as st

state = st.session_state
cookies = state["cookies"]

state["userid"] = ""
state["password"] = ""
cookies["userid"] = ""
cookies["password"] = ""

st.switch_page("pages/home.py")
