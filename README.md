# FreeLake
使用`streamlit`构建的论坛程序，包括基础的功能，如用户管理、文章发布评论等，并使用`peewee`快速构建使用数据库。

---

## 使用
### Streamlit试用
进入该网站以查看效果：https://freelake.streamlit.app/

![Streamlit试用](preview.png)

### 本地部署
1. 克隆仓库
    ```bash
    git clone https://github.com/XiangQinxi/freelake.git
    cd freelake
    ```
2. 安装依赖
    ```bash
    pip install -r requirements.txt
    ```

3. 运行程序
    ```bash
    streamlit run main.py
    ```

## 计划
- [ ] 首页文章展示最新一条评论
- [ ] 完善举报功能