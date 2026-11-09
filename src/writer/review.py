"""模型输出解析：审核 JSON 提取 + 新书设定分段标记解析。"""
from __future__ import annotations

import json
import re

from ..llm import strip_think

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.S)
_FENCE_RE = re.compile(r"```[a-zA-Z]*\s*")


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text or "")


def extract_json_block(text: str) -> dict | None:
    m = _JSON_BLOCK_RE.search(_strip_fences(strip_think(text)))
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


# ---- 新书设定：分段标记格式（比 JSON 对本地模型鲁棒得多） ----

_SECTION_RE = re.compile(r"【(书名|题材|世界观|人物|大纲)】")
_CHAR_LINE_RE = re.compile(r"[-•*]?\s*(?:\*\*)?([^：:*\n]{1,12})(?:\*\*)?[：:]\s*(.+)")


def parse_novel_setting(text: str) -> dict | None:
    """解析【书名】/【题材】/【世界观】/【人物】/【大纲】分段文本，失败返回 None。"""
    t = _strip_fences(strip_think(text))
    matches = list(_SECTION_RE.finditer(t))
    if not matches:
        return None
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(t)
        sections[m.group(1)] = t[m.end():end].strip()
    title = sections.get("书名", "").strip().splitlines()[0].strip("《》* ") if sections.get("书名") else ""
    if not title:
        return None
    characters = []
    for line in sections.get("人物", "").splitlines():
        line = line.strip()
        cm = _CHAR_LINE_RE.match(line)
        if cm:
            characters.append({"name": cm.group(1).strip(), "persona": cm.group(2).strip()})
    return {
        "title": title,
        "genre": sections.get("题材", "").splitlines()[0].strip() if sections.get("题材") else "",
        "worldview": sections.get("世界观", "").strip(),
        "characters": characters,
        "outline": sections.get("大纲", "").strip(),
    }
