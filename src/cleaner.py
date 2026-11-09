"""输出清洗管道（纯函数）：把模型输出剥成"只有正文"。

层级：思维链剥离 → markdown 剥离 → 元话语剥离 → 纯度校验。
清洗失败的段整段拒绝，由上层决定重试。
"""
from __future__ import annotations

import re

from .llm import strip_think

# markdown 噪音符号（标题/加粗/斜体/代码/引用/删除线/表格/列表符/分隔线）
_MD_NOISE_RE = re.compile(r"[*_#`>\[\]~|]+|^---+\s*$|^===+\s*$", re.M)

# 常见元话语：AI 自言自语、章节标注、作者按语、对读者说话
_META_LINE_RE = re.compile(
    r"^(好的|当然|以下是|下面是|这是|我写|续写如下|本章|第[一二三四五六七八九十百千0-9]+章.*$|"
    r"【.*】|（.*按.*）|\(.*按.*\)).*$",
    re.M,
)
_META_INLINE_RE = re.compile(r"（(?:注|备注|作者|按语|说明)[^）]*）|\((?:注|备注|作者|按语|说明)[^)]*\)")

# 对前文指手画脚的元话语（衔接校验用）
_FORBIDDEN_RE = re.compile(
    r"前文(?:提到|说道|写道)|上一[段章](?:提到|说道|写道)|综上所述|总而言之|"
    r"以下是|以上内容|作为(?:一?个)?AI|继续续写|待续|未完待续"
)

# 纯度校验阈值
_MIN_CHINESE_RATIO = 0.5
_MIN_PARA_LEN = 30


def clean_paragraph(text: str) -> str:
    """剥离思维链/markdown/元话语，返回净化后的正文。"""
    t = strip_think(text or "")
    t = _MD_NOISE_RE.sub("", t)
    t = _META_LINE_RE.sub("", t)
    t = _META_INLINE_RE.sub("", t)
    # 合并清理产生的空行
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def is_pure_narrative(text: str, min_len: int = _MIN_PARA_LEN) -> tuple[bool, str]:
    """段落纯度校验：非空、长度达标、中文为主、不含元话语残留。"""
    t = (text or "").strip()
    if not t:
        return False, "空段落"
    if len(t) < min_len:
        return False, f"过短（{len(t)} < {min_len} 字）"
    chinese = sum(1 for ch in t if "一" <= ch <= "鿿")
    if chinese / max(1, len(t)) < _MIN_CHINESE_RATIO:
        return False, "中文占比过低（疑似非正文内容）"
    m = _FORBIDDEN_RE.search(t)
    if m:
        return False, f"含元话语：{m.group(0)!r}"
    return True, ""


def strip_hook_echo(text: str, prev_hook: str = "") -> str:
    """剥离模型复读的上一段钩子（小模型常见行为：先复述钩子句再续写）。

    完整复读：直接去掉钩子前缀；前缀复读（改了句尾/截断）：按首句剥。
    剥完为空则保留原文（交由规则校验处理）。
    """
    t = (text or "").strip()
    hook = (prev_hook or "").strip()
    if not t or not hook or not ("一" <= hook[0] <= "鿿"):
        return t
    if t.startswith(hook):
        rest = t[len(hook):].lstrip("，。！？；、： \n　")
        return rest if rest else t
    head = hook[:12]
    if len(hook) >= 12 and t.startswith(head):
        m = re.match(r"^[^。！？!?…]*[。！？!?…]", t)
        if m:
            rest = t[m.end():].lstrip("，。！？；、： \n　")
            return rest if rest else t
    return t


def check_paragraph(text: str, prev_hook: str = "",
                    min_len: int = _MIN_PARA_LEN) -> tuple[bool, list[str]]:
    """段级规则校验：纯度 + 收句 + 钩子衔接。返回 (是否通过, 问题列表)。"""
    issues: list[str] = []
    ok, reason = is_pure_narrative(text, min_len=min_len)
    if not ok:
        issues.append(reason)
    t = (text or "").strip()
    # 收句校验：段尾必须是完整句（句末标点收尾，允许其后跟引号/书名号等收束符）。
    # 否则章末会断在半句话上，下一章又接着写，直播观感极差。
    if t and not re.search(r"[。！？!?…][”』」）)]?$", t):
        issues.append("段尾未收句（必须以。！？…等句末标点结束）")
    hook_head = (prev_hook or "").strip()[:12]
    if hook_head and t.startswith(hook_head):
        issues.append("新段开头重复了上一段的钩子原文")
    return (not issues), issues


def extract_hook(text: str) -> str:
    """提取段尾钩子：最后一句（含句末标点）。"""
    t = (text or "").strip()
    if not t:
        return ""
    parts = re.split(r"(?<=[。！？!?…])", t)
    parts = [p for p in parts if p.strip()]
    # 句末引号被切成独立片段（如 ……这一切。" → '"'）：并回前一句
    if len(parts) >= 2 and not any("一" <= c <= "鿿" for c in parts[-1]):
        parts[-2] += parts[-1]
        parts.pop()
    return parts[-1].strip() if parts else t
