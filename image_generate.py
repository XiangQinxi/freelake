import base64
import urllib.request

import streamlit as st
from openai import OpenAI

st.page_link("home.py", label="返回主页")

st.subheader("图片生成")

secrets = st.secrets


def get_resolution(ratio: str, scale: float, base: int = 1024) -> str:
    # 解析宽高比，支持 "a:b" 或 "a/b"
    if ":" in ratio:
        a_str, b_str = ratio.split(":")
    elif "/" in ratio:
        a_str, b_str = ratio.split("/")
    else:
        raise ValueError("宽高比格式错误，请使用 'a:b' 或 'a/b' 的形式")

    # 转换为数值（通常为整数，但也可兼容浮点数）
    a = float(a_str.strip())
    b = float(b_str.strip())
    if b == 0:
        raise ValueError("宽高比分母不能为零")
    aspect = a / b

    # 根据横竖屏选择参考边
    if aspect >= 1:  # 横屏或正方形：以宽度为基准
        target_width = base * scale
        target_height = target_width / aspect
    else:  # 竖屏：以高度为基准
        target_height = base * scale
        target_width = target_height * aspect

    # 四舍五入取整（可选择调整到最近的偶数，这里保持直接取整）
    width = int(round(target_width))
    height = int(round(target_height))

    return f"{width}x{height}"


with st.container(border=True):
    uploaded_image = st.file_uploader(
        "上传图片", type=["png", "jpg", "jpeg"], accept_multiple_files=False
    )
    if uploaded_image:
        with st.expander("查看图片", expanded=True):
            st.image(
                uploaded_image,
                caption=uploaded_image.name,  # noqa: NOQA
                width=300,
            )

    prompt = st.text_area("请输入图片描述", height=100)

    quality = st.select_slider(
        "质量 (quality)",
        options=["auto", "standard", "low", "medium", "high"],
        value="low",
        help="low 省 token，测试阶段用 low 即可；high 效果最好但贵",
    )
    output_format = st.selectbox(
        "输出格式 (output_format)",
        options=["png", "jpeg", "webp"],
        index=0,
        help="png 支持透明背景，jpeg 体积小",
    )
    size: str = st.selectbox(
        "图片尺寸 (size)",
        options=[
            "1024x1024",
            "1536x1024",
            "1024x1536",
            "2048x2048",
            "2048x1152",
            "3840x2160",
            "2160x3840",
            "auto",
        ],
        index=0,
    )

    number: int = st.number_input(
        "生成数量", min_value=1, max_value=10, value=1
    )  # noqa: NOQA

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
                            timeout=360,
                        )
                else:
                    with st.spinner("✏️ 正在编辑图片，请稍候..."):
                        result = client.images.edit(  # noqa: NOQA
                            model="gpt-image-2",
                            image=uploaded_image,  # ✅ 直接传 UploadedFile (BytesIO)
                            prompt=prompt,
                            size=size,
                            quality=quality,
                            n=number,
                            timeout=360,
                        )

                for i in range(number):
                    data = result.data[i]  # NOQA: noqa: F841  # noqa: NOQA
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
