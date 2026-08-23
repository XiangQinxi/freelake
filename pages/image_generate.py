"""
FreeLake 图片生成（pages/image_generate.py）
============================================

管理员专属页面：调用 OpenAI 兼容接口（gpt-image-2）根据文字描述生成
图片，或对上传的图片进行编辑。API 密钥与接口地址来自
``.streamlit/secrets.toml`` 的 ``[ai]`` 段。

注意：页面级 ``st.set_page_config`` 不可用——入口 main.py 已统一设置，
在 st.navigation 的页面中重复调用会报错。
"""

import base64
import urllib.request

import streamlit as st
from openai import OpenAI

st.page_link("pages/home.py", label=":material/arrow_back: 返回主页")
st.subheader(":material/image: 图片生成")
st.caption("使用 AI 根据文字描述生成图片，或对上传的图片进行编辑。")


def _ai_config() -> dict:
    """安全读取 [ai] 配置，secrets 缺失或未配置时返回空字典。"""
    try:
        return st.secrets.get("ai", {}) or {}
    except Exception:
        return {}


ai_cfg = _ai_config()
if not (ai_cfg.get("api_key") and ai_cfg.get("base_url")):
    st.info(
        "尚未配置 AI 图片生成密钥。请在 `.streamlit/secrets.toml` 的 `[ai]` 段填写 "
        "`base_url` 与 `api_key`（或在 Streamlit Cloud 的 Secrets 中配置），配置后刷新本页即可使用。",
        icon=":material/auto_awesome:",
    )
    st.stop()

if "image_results" not in st.session_state:
    st.session_state.image_results = []
if "generating" not in st.session_state:
    st.session_state.generating = False


def get_resolution(ratio: str, scale: float, base: int = 1024) -> str:
    if ":" in ratio:
        a_str, b_str = ratio.split(":")
    elif "/" in ratio:
        a_str, b_str = ratio.split("/")
    else:
        raise ValueError("宽高比格式错误，请使用 'a:b' 或 'a/b' 的形式")

    a = float(a_str.strip())
    b = float(b_str.strip())
    if b == 0:
        raise ValueError("宽高比分母不能为零")
    aspect = a / b

    if aspect >= 1:
        target_width = base * scale
        target_height = target_width / aspect
    else:
        target_height = base * scale
        target_width = target_height * aspect

    width = int(round(target_width))
    height = int(round(target_height))

    return f"{width}x{height}"


with st.container(border=True):
    uploaded_image = st.file_uploader(
        "上传图片", type=["png", "jpg", "jpeg"], accept_multiple_files=False
    )
    if uploaded_image:
        with st.expander("查看图片", expanded=True):
            st.image(uploaded_image, caption=uploaded_image.name, width=300)

    prompt = st.text_area(
        "图片描述",
        placeholder="描述你想要的画面，细节越具体效果越好",
        height=100,
    )

    quality = st.select_slider(
        "质量",
        options=["auto", "standard", "low", "medium", "high"],
        value="low",
        help="对应接口参数 quality：low 最省 token，high 效果最好但更贵",
    )
    output_format = st.selectbox(
        "输出格式",
        options=["png", "jpeg", "webp"],
        index=0,
        help="对应接口参数 output_format：png 支持透明背景，jpeg 体积更小",
    )
    size: str = st.selectbox(
        "图片尺寸",
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
        help="对应接口参数 size；auto 由服务端自动决定",
    )

    number: int = st.number_input(
        "生成数量", min_value=1, max_value=10, value=1, help="单次最多生成 10 张"
    )

    @st.fragment
    def generation_ui(prompt, quality, output_format, size, number, uploaded_image):
        if st.button(
            ":material/image: 生成图片",
            disabled=st.session_state.generating,
            type="primary",
        ):
            if not prompt:
                st.warning("请输入图片描述")
            else:
                st.session_state.generating = True
                try:
                    client = OpenAI(
                        api_key=ai_cfg["api_key"],
                        base_url=ai_cfg["base_url"],
                    )
                    st.info(
                        "提示：如需渲染特定文字，建议用引号括起来，如「深夜食堂」",
                        icon=":material/lightbulb:",
                    )
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
                            result = client.images.edit(
                                model="gpt-image-2",
                                image=uploaded_image,
                                prompt=prompt,
                                size=size,
                                quality=quality,
                                n=number,
                                timeout=360,
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
                        st.session_state.image_results.append(
                            {
                                "img_bytes": img_bytes,
                                "ext": output_format or "png",
                            }
                        )
                    st.success("生成成功", icon=":material/check_circle:")

                except Exception as e:
                    st.error(f"生成失败：{e}", icon=":material/error:")
                finally:
                    st.session_state.generating = False

        for idx, item in enumerate(st.session_state.image_results):
            st.image(item["img_bytes"], width="stretch")
            st.download_button(
                label=f":material/download: 下载图片 {idx + 1}",
                data=item["img_bytes"],
                file_name=f"generated_image_{idx + 1}.{item['ext']}",
                mime=f"image/{item['ext']}",
                key=f"download_{idx}",
            )

    generation_ui(prompt, quality, output_format, size, number, uploaded_image)
