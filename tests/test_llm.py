"""LLM 客户端单测（T2）：全部走 httpx MockTransport，不发生真实网络请求。"""

import json

import httpx
import pytest

from src.config import LLMPresetConfig, LLMProfileConfig
from src.llm import LLMError, SentenceStream, resolve_provider, stream_chat, strip_think


def sse_response(deltas: list[str]) -> httpx.Response:
    """构造 OpenAI 兼容 SSE 流式响应。"""
    lines = []
    for d in deltas:
        obj = {"choices": [{"delta": {"content": d}}]}
        lines.append(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n")
    lines.append("data: [DONE]\n\n")
    return httpx.Response(200, content="".join(lines).encode("utf-8"))


async def collect(profile, messages, transport) -> list[str]:
    return [d async for d in stream_chat(profile, messages, transport=transport)]


@pytest.mark.asyncio
async def test_stream_yields_deltas_in_order():
    deltas = ["夜风", "掠过", "山岗。"]
    transport = httpx.MockTransport(lambda req: sse_response(deltas))
    profile = LLMProfileConfig(preset="deepseek", model="test-model", api_key="k")
    out = await collect(profile, [{"role": "user", "content": "hi"}], transport)
    assert out == deltas


@pytest.mark.asyncio
async def test_bearer_auth_and_url():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        return sse_response(["好"])

    transport = httpx.MockTransport(handler)
    profile = LLMProfileConfig(preset="deepseek", model="m", api_key="sk-123")
    await collect(profile, [{"role": "user", "content": "x"}], transport)
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["auth"] == "Bearer sk-123"


@pytest.mark.asyncio
async def test_lmstudio_no_auth_default_url():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        return sse_response(["ok"])

    transport = httpx.MockTransport(handler)
    profile = LLMProfileConfig(preset="lmstudio", model="google/gemma-4-e4b")
    await collect(profile, [{"role": "user", "content": "x"}], transport)
    assert captured["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert captured["auth"] is None


@pytest.mark.asyncio
async def test_custom_preset_from_config():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        return sse_response(["ok"])

    transport = httpx.MockTransport(handler)
    custom = {"my_gateway": {"base_url": "https://gw.example.com/v1", "auth": "bearer", "api_key": "pk-1"}}
    profile = LLMProfileConfig(preset="my_gateway", model="m")
    out = [
        d
        async for d in stream_chat(
            profile, [{"role": "user", "content": "x"}], custom_presets=custom, transport=transport
        )
    ]
    assert out == ["ok"]
    assert captured["url"] == "https://gw.example.com/v1/chat/completions"


def test_resolve_provider_rules():
    # 内置预设解析
    r = resolve_provider(LLMProfileConfig(preset="volcengine", model="ep-1"))
    assert r["base_url"].startswith("https://ark.cn-beijing")
    # 未知预设报错
    with pytest.raises(LLMError):
        resolve_provider(LLMProfileConfig(preset="no_such", model="m"))
    # 自定义预设缺 base_url 报错
    with pytest.raises(LLMError):
        resolve_provider(LLMProfileConfig(preset="bad", model="m"), {"bad": {"base_url": ""}})


def test_strip_think():
    assert strip_think("<think>推理过程</think>正文") == "正文"
    assert strip_think("没有标记") == "没有标记"


def test_sentence_stream():
    ss = SentenceStream()
    assert ss.feed("第一句。第二") == ["第一句。"]
    assert ss.feed("句，够了。尾") == ["第二句，够了。"]
    assert ss.flush() == "尾"
