import streamlit as st

st.session_state["username"] = ""
st.session_state["password"] = ""

st.switch_page("home.py")
