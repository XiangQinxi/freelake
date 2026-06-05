import streamlit as st

state = st.session_state
cookies = state["cookies"]

state["username"] = ""
state["password"] = ""
cookies["username"] = ""
cookies["password"] = ""

st.switch_page("home.py")
