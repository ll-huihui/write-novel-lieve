"""弹幕结构性攻击拦截（纯函数，可单测）。

实战经验（cyber-xiaoya-live 2026-10）：观众发 XSS 代码与提示词注入
（「忽略前面所有指令」等）被复读/喂 LLM 会污染回复。命中 = 整条丢弃：
不进闲聊历史、不回复、不展示。这是代码级常开防线，与配置无关。
"""
from __future__ import annotations

import re

_BLOCK_RES = tuple(re.compile(p, re.I) for p in (
    r"<\s*/?\s*(script|textarea|iframe|img|svg|style|link|input|form|body|html)\b",
    r"<\?php|javascript\s*:|\bon(?:error|load|click|mouseover)\s*=|alert\s*\(",
    r"忽略.{0,10}指令|无视.{0,8}(设定|规则)|忘记.{0,8}(设定|规则|身份)",
    r"停止(朗读|念|复读)",
    r"(进入|开启|切换|激活).{0,6}无(限制|约束|审查)|无(限制|约束)(模式|状态)",
    r"system\s*prompt|jailbreak|越狱",
))


def is_blocked(text: str) -> str | None:
    """命中返回命中的片段（供日志），未命中返回 None。"""
    for pattern in _BLOCK_RES:
        m = pattern.search(text)
        if m:
            return m.group(0)[:20]
    return None
