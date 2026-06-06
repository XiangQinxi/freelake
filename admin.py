import streamlit as st
from streamlit_file_browser import st_file_browser

import os
import pandas as pd

from api import User, Post, execute_sql

st.subheader("管理员页面")

with st.expander("数据库管理", expanded=True):
    with st.expander("用户管理", expanded=True):
        user = User()

        df = pd.DataFrame(user.get_all())
        st.dataframe(df, use_container_width=True)

    with st.expander("文章管理", expanded=True):
        post = Post()

        df = pd.DataFrame(post.get_all())
        st.dataframe(df, use_container_width=True)

    col1, col2 = st.columns([0.8, 0.2], vertical_alignment="bottom")
    query = col1.text_input("请输入SQL查询语句", placeholder="例如：SELECT * FROM user")
    if col2.button("执行查询"):
        if query:
            try:
                result = execute_sql(query)
                st.write(result)
            except Exception as e:
                st.error(f"查询执行失败：{e}")
        else:
            st.warning("请输入SQL查询语句")

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