"""douyinLive 原始消息 → 归一化弹幕（纯函数，可直接单测）。

归一化结果：{kind, nick, content[, gift_item, gift_num]}；
与本工具无关的消息（system/成员进出/点赞等）返回 None。
"""
from __future__ import annotations

import re

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_NICK_MAX = 20
_CONTENT_MAX = 100

GIFT_METHODS = ("WebcastGiftMessage", "WebcastLightGiftMessage", "WebcastBindingGiftMessage")


def _as_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def clean_text(s: str, max_len: int) -> str:
    """展示层清洗：去控制字符、截断。注入过滤在 T6 闲聊通路另行处理。"""
    return _CTRL_RE.sub("", s or "").strip()[:max_len]


def normalize(raw) -> dict | None:
    """douyinLive 一条消息 → 归一化弹幕；不关心的消息返回 None。"""
    if not isinstance(raw, dict) or raw.get("type") == "system":
        return None
    method = raw.get("method") or ""
    nick = clean_text(str((raw.get("user") or {}).get("nickname") or ""), _NICK_MAX)

    if method == "WebcastChatMessage":
        content = clean_text(str(raw.get("content") or ""), _CONTENT_MAX)
        if not nick or not content:
            return None
        return {"kind": "chat", "nick": nick, "content": content}

    if method in GIFT_METHODS:
        inner = raw.get("msg") if method == "WebcastBindingGiftMessage" else raw
        if not isinstance(inner, dict) or not inner:
            return None
        gift = inner.get("gift") or {}
        item = clean_text(str(gift.get("name") or ""), 30)
        if not nick or not item:
            return None
        # 连击礼物连续推帧：只在「连击结束」或非连击礼物时计入，避免数量算重
        if inner.get("groupId") and not _as_int(inner.get("repeatEnd")):
            return None
        num = max(_as_int(inner.get("repeatCount")), _as_int(inner.get("comboCount")),
                  _as_int(inner.get("totalCount")), _as_int(inner.get("count")), 1)
        return {"kind": "gift", "nick": nick, "content": f"送出 {item}×{num}",
                "gift_item": item, "gift_num": num}

    return None
