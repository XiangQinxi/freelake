import base64
import urllib.request

import streamlit as st
from openai import OpenAI

st.page_link("home.py", label="返回主页")

st.subheader("图片生成")

secrets = st.secrets

with st.container(border=True):
    uploaded_image = st.file_uploader(
        "上传图片", type=["png", "jpg", "jpeg"], accept_multiple_files=False
    )
    if uploaded_image:
        with st.expander("查看图片", expanded=True):
            st.image(
                uploaded_image,
                caption=uploaded_image.name,
                width=300,
            )

    prompt = st.text_area("请输入图片描述", height=100)

    quality = st.select_slider(
        "质量 (quality)",
        options=["low", "medium", "high"],
        value="low",
        help="low 省 token，测试阶段用 low 即可；high 效果最好但贵",
        key="tab1_quality",
    )
    output_format = st.selectbox(
        "输出格式 (output_format)",
        options=["png", "jpeg", "webp"],
        index=0,
        help="png 支持透明背景，jpeg 体积小",
        key="tab1_format",
    )
    size = st.selectbox(
        "图片尺寸 (size)",
        options=["1024x1024", "1024x1536", "1536x1024", "auto"],
        index=0,
        key="tab1_size",
    )
    number: int = st.number_input("生成数量", min_value=1, max_value=10, value=1)

    if st.button("生成图片"):
        if not prompt:
            st.warning("请输入图片描述")
        else:
            try:
                client = OpenAI(
                    api_key=secrets["ai"]["api_key"], base_url=secrets["ai"]["base_url"]
                )
                st.info("💡 提示：如需渲染特定文字，建议用引号括起来，如「深夜食堂」")

                if not uploaded_image:
                    with st.spinner("🎨 正在生成图片，请稍候..."):
                        result = client.images.generate(
                            model="gpt-image-2",
                            prompt=prompt,
                            size=size,
                            quality=quality,
                            output_format=output_format,
                            n=number,
                        )
                else:
                    with st.spinner("✏️ 正在编辑图片，请稍候..."):
                        result = client.images.edit(
                            model="gpt-image-2",
                            image=uploaded_image,  # ✅ 直接传 UploadedFile (BytesIO)
                            prompt=prompt,
                            size=size,
                            quality=quality,
                            n=number,
                        )

                for i in range(number):
                    data = result.data[i]
                    if data.b64_json:
                        img_bytes = base64.b64decode(data.b64_json)
                    elif data.url:
                        with urllib.request.urlopen(data.url) as resp:
                            img_bytes = resp.read()
                    else:
                        st.warning(f"⚠️ 第 {i + 1} 张图片：未能获取到图片数据。")
                        continue
                    st.image(img_bytes, use_container_width=True)
                    ext = output_format or "png"
                    st.download_button(
                        label=f"💾 下载图片 {i + 1} (.{ext})",
                        data=img_bytes,
                        file_name=f"generated_image_{i + 1}.{ext}",
                        mime=f"image/{ext}",
                    )
                st.success("✅ 生成成功！")

            except Exception as e:
                st.error(f"❌ 生成失败：{str(e)}")
            st.success("图片生成成功！")
