"""
文本高亮辅助（freelake/web/text.py）
====================================

搜索关键词与 @提及 的彩色标记。纯字符串处理，不依赖 Streamlit 状态。
"""

import re


def highlight_keyword(text: str, keyword: str) -> str:
    """将关键词匹配片段用 Streamlit 彩色文本（:red[...]）高亮；无关键词或空文本则原样返回。"""
    if not keyword or not text:
        return text
    pattern = re.compile(re.escape(keyword), re.IGNORECASE)
    return pattern.sub(lambda m: f":red[{m.group(0)}]", text)


def highlight_mentions(text: str) -> str:
    """把评论/内容中的 @用户名 渲染为醒目徽章（不做真实用户解析，仅视觉高亮）。"""
    if not text:
        return text
    # 匹配中英文、数字、下划线、连字符组成的 @提及
    return re.sub(
        r"@([A-Za-z0-9_\-\u4e00-\u9fa5]+)",
        r":blue-badge[@\1]",
        text,
    )
