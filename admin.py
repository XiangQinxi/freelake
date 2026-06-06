import streamlit as st
from streamlit_file_browser import st_file_browser

import os

st.subheader("管理员页面")

with st.expander("文件管理", expanded=True):
    event = st_file_browser(os.path.dirname(os.path.abspath(__file__)),
        key="deep",
        show_choose_file=True,
        show_delete_file=True,
        show_download_file=False,
        show_new_folder=True,
        show_upload_file=False,
    )
    st.write(event)