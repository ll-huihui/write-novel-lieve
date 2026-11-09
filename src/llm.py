"""LLM 流式客户端：OpenAI 兼容 /chat/completions，多预设（云端平台 + 本地运行时）。

预设只决定 base_url 与鉴权方式；写作/闲聊两个角色档位引用预设并可覆盖
model / api_key / temperature（两段式配置，见 config.example.json 的 llm 段）。

本层无状态：调用方传入完整 messages，历史由上层管理
（写作=上下文组装器，闲聊=独立 deque）。
"""
from __future__ import annotations

import json
import re
from typing import AsyncIterator

import httpx

# ---- 内置预设表：预设名 -> (base_url, 鉴权方式) ----
# 新增预设 = 加一行；表里没有的平台用 config llm.presets 自定义，不用改代码。
PRESETS: dict[str, dict] = {
    # 云端平台（Bearer key，OpenAI 兼容端点）
    "openai":      {"base_url": "https://api.openai.com/v1", "auth": "bearer"},
    "anthropic":   {"base_url": "https://api.anthropic.com/v1", "auth": "bearer"},
    "gemini":      {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "auth": "bearer"},
    "deepseek":    {"base_url": "https://api.deepseek.com", "auth": "bearer"},
    "volcengine":  {"base_url": "https://ark.cn-beijing.volces.com/api/v3", "auth": "bearer"},
    "qwen":        {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "auth": "bearer"},
    "moonshot":    {"base_url": "https://api.moonshot.cn/v1", "auth": "bearer"},
    "zhipu":       {"base_url": "https://open.bigmodel.cn/api/paas/v4", "auth": "bearer"},
    "qianfan":     {"base_url": "https://qianfan.baidubce.com/v2", "auth": "bearer"},
    "minimax":     {"base_url": "https://api.minimaxi.com/v1", "auth": "bearer"},
    "stepfun":     {"base_url": "https://api.stepfun.com/v1", "auth": "bearer"},
    "lingyi":      {"base_url": "https://api.lingyiwanwu.com/v1", "auth": "bearer"},
    "siliconflow": {"base_url": "https://api.siliconflow.cn/v1", "auth": "bearer"},
    "openrouter":  {"base_url": "https://openrouter.ai/api/v1", "auth": "bearer"},
    "groq":        {"base_url": "https://api.groq.com/openai/v1", "auth": "bearer"},
    "xai":         {"base_url": "https://api.x.ai/v1", "auth": "bearer"},
    "mistral":     {"base_url": "https://api.mistral.ai/v1", "auth": "bearer"},
    # 本地运行时（无鉴权）
    "lmstudio":    {"base_url": "http://127.0.0.1:1234/v1", "auth": "none"},
    "ollama":      {"base_url": "http://127.0.0.1:11434/v1", "auth": "none"},
    "vllm":        {"base_url": "http://127.0.0.1:8000/v1", "auth": "none"},
}

_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)

# 强停顿(必须分句)与弱停顿(句子够长时才分)，T7 语音合成复用
STRONG_ENDS = "。！？!?；;\n"
SOFT_ENDS = "，,、"


def strip_think(text: str) -> str:
    """剥离 DeepSeek 等模型的思维链标记。"""
    return _THINK_RE.sub("", text).strip()


class SentenceStream:
    """增量分句：feed(增量文本) → 本轮凑齐的完整句子列表；flush() 收尾。

    强停顿立即分句；弱停顿只在已积累 ≥ soft_min 字时分；缓冲超过
    hard_max 字强制放出（无标点长串兜底）。
    """

    def __init__(self, soft_min: int = 10, hard_max: int = 30):
        self.buf = ""
        self.soft_min = soft_min
        self.hard_max = hard_max

    def feed(self, delta: str) -> list[str]:
        self.buf += delta or ""
        out: list[str] = []
        while True:
            cut = -1
            for i, ch in enumerate(self.buf):
                if ch in STRONG_ENDS:
                    cut = i
                    break
                if ch in SOFT_ENDS and i + 1 >= self.soft_min:
                    cut = i
                    break
            if cut < 0:
                if len(self.buf) > self.hard_max:
                    out.append(self.buf[: self.hard_max].strip())
                    self.buf = self.buf[self.hard_max:]
                    continue
                break
            piece = self.buf[: cut + 1].strip()
            self.buf = self.buf[cut + 1:]
            if piece:
                out.append(piece)
        return out

    def flush(self) -> str:
        tail = self.buf.strip()
        self.buf = ""
        return tail


class LLMError(RuntimeError):
    pass


def resolve_provider(profile, custom_presets: dict | None = None) -> dict:
    """解析角色档位 -> {base_url, auth, api_key, model, temperature, max_tokens, thinking}。

    解析顺序：内置预设表 / config llm.presets 自定义预设 → 角色档位覆盖默认值。
    """
    name = (profile.preset or "").strip()
    if name in PRESETS:
        base = dict(PRESETS[name])
    elif custom_presets and name in custom_presets:
        p = custom_presets[name]
        base = {
            "base_url": (p.get("base_url") or "").strip(),
            "auth": (p.get("auth") or "bearer").strip(),
        }
        if not base["base_url"]:
            raise LLMError(f"自定义预设 {name!r} 缺少 base_url")
    else:
        known = ", ".join(sorted(PRESETS))
        raise LLMError(f"未知 LLM 预设 {name!r}（可用: {known}，或在 llm.presets 中自定义）")

    base["api_key"] = (profile.api_key or base.get("api_key", "") or "").strip()
    base["model"] = profile.model
    base["temperature"] = profile.temperature
    base["max_tokens"] = profile.max_tokens
    base["thinking"] = (profile.thinking or "").strip().lower()
    return base


async def stream_chat(
    profile,
    messages: list[dict],
    custom_presets: dict | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[str]:
    """流式对话：yield 增量文本。异常抛 LLMError。本层不写历史。"""
    c = resolve_provider(profile, custom_presets)
    if not c["model"]:
        raise LLMError("LLM 模型未配置（llm 段对应角色的 model 字段）")

    body: dict = {
        "model": c["model"],
        "messages": messages,
        "stream": True,
        "temperature": float(c["temperature"]),
        "max_tokens": int(c["max_tokens"]),
    }
    if c["thinking"] in ("enabled", "disabled"):
        body["thinking"] = {"type": c["thinking"]}

    headers = {"Content-Type": "application/json"}
    if c["auth"] == "bearer" and c["api_key"]:
        headers["Authorization"] = f"Bearer {c['api_key']}"

    url = c["base_url"].rstrip("/") + "/chat/completions"
    timeout = httpx.Timeout(30.0, read=600.0)
    async with httpx.AsyncClient(verify=True, timeout=timeout, transport=transport) as client:
        async with client.stream("POST", url, headers=headers, json=body) as r:
            if r.status_code >= 400:
                text = (await r.aread()).decode("utf-8", "ignore")
                raise LLMError(f"LLM HTTP {r.status_code}: {text[:300]}")
            async for line in r.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue
                choice = (obj.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                piece = delta.get("content") or ""
                if piece:
                    yield piece
                # 截断检测：max_tokens 用尽或上下文超限时模型输出不完整，
                # 这种段落不能入库（衔接校验对不完整文本无意义）
                if choice.get("finish_reason") == "length":
                    raise LLMError("模型输出被截断（max_tokens 或上下文窗口不足）")
