"""弹幕归一化 / 链接解析 / mock 流单测（T5）。"""

import json

import pytest

from src.danmaku import DanmakuClient, normalize, parse_live_input
from src.broadcaster import StageBroadcaster
from src.config import Config


# ---- normalize：douyinLive 原始消息 → 归一化弹幕 ----

def test_normalize_chat():
    raw = {"method": "WebcastChatMessage", "user": {"nickname": "追更的猫"},
           "content": "这个开头有点东西！"}
    m = normalize(raw)
    assert m == {"kind": "chat", "nick": "追更的猫", "content": "这个开头有点东西！"}


def test_normalize_gift():
    raw = {"method": "WebcastGiftMessage", "user": {"nickname": "大佬袁"},
           "gift": {"name": "玫瑰", "diamond_count": 1}, "repeatCount": 3}
    m = normalize(raw)
    assert m["kind"] == "gift" and m["gift_item"] == "玫瑰" and m["gift_num"] == 3


def test_normalize_gift_combo_middle_frame_skipped():
    """连击中间帧去重，结束帧计入。"""
    base = {"method": "WebcastGiftMessage", "user": {"nickname": "大佬袁"},
            "gift": {"name": "玫瑰"}, "repeatCount": 5, "groupId": "g1"}
    assert normalize({**base, "repeatEnd": 0}) is None
    m = normalize({**base, "repeatEnd": 1})
    assert m is not None and m["gift_num"] == 5


def test_normalize_ignores_system_and_unknown():
    assert normalize({"type": "system", "code": 0}) is None
    assert normalize({"method": "WebcastMemberMessage", "user": {"nickname": "x"}}) is None
    assert normalize({"method": "WebcastChatMessage", "user": {}, "content": ""}) is None
    assert normalize("not a dict") is None


def test_normalize_cleans_text():
    raw = {"method": "WebcastChatMessage",
           "user": {"nickname": "很长的昵称一二三四五六七八九十十一十二十三"},
           "content": "  带控制字符\x07的内容  "}
    m = normalize(raw)
    assert len(m["nick"]) <= 20 and "\x07" not in m["content"]
    assert m["content"] == "带控制字符的内容"


# ---- parse_live_input：房间号 / 链接 / 分享口令 ----

def test_parse_bare_room_id():
    assert parse_live_input("745961234") == {"room": "745961234"}
    assert parse_live_input("  some-room_1 ") == {"room": "some-room_1"}


def test_parse_web_url():
    assert parse_live_input("https://live.douyin.com/745961234?enter_from=share") == {"room": "745961234"}
    assert parse_live_input("来看直播 https://live.douyin.com/12345 一起") == {"room": "12345"}


def test_parse_share_text_with_short_link():
    text = "7长按复制此条消息，打开抖音搜索 https://v.douyin.com/iRNBho6u/ @抖音直播"
    assert parse_live_input(text) == {"short_url": "https://v.douyin.com/iRNBho6u/"}


def test_parse_unrecognized():
    assert parse_live_input("") == {}
    assert parse_live_input("随便说的一句话没有链接") == {}


@pytest.mark.asyncio
async def test_resolve_room_short_link_follows_redirect(monkeypatch):
    """短链解析：跟 302 后从最终 URL 取房间号。"""
    from src.danmaku import resolve_room

    class FakeResp:
        url = "https://live.douyin.com/987654321?from=share"

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    import src.danmaku.resolve as mod
    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
    assert await resolve_room("https://v.douyin.com/abcXYZ/") == "987654321"


# ---- DanmakuClient：mock 流 + 状态广播 ----

class DummyBC:
    def __init__(self):
        self.msgs = []

    async def broadcast(self, msg):
        self.msgs.append(msg)


@pytest.mark.asyncio
async def test_client_mock_flow(monkeypatch):
    import src.danmaku.client as mod

    async def fast_stream():
        yield json.dumps({"method": "WebcastChatMessage",
                          "user": {"nickname": "测试"}, "content": "你好"}, ensure_ascii=False)
        yield json.dumps({"method": "WebcastGiftMessage", "user": {"nickname": "礼"},
                          "gift": {"name": "小心心"}, "repeatCount": 1})

    monkeypatch.setattr(mod, "mock_stream", fast_stream)
    bc = DummyBC()
    client = DanmakuClient(Config(), bc)
    st = await client.connect("", mock=True)
    assert st["status"] in ("connected", "connecting")
    for _ in range(50):  # 等两条消息处理完
        if len([m for m in bc.msgs if m["type"] == "danmaku"]) >= 2:
            break
        await __import__("asyncio").sleep(0.02)
    dm = [m for m in bc.msgs if m["type"] == "danmaku"]
    assert dm[0]["kind"] == "chat" and dm[0]["nick"] == "测试"
    assert dm[1]["kind"] == "gift" and dm[1]["gift_item"] == "小心心"
    out = await client.disconnect()
    assert out["status"] == "disconnected" and not client.running


@pytest.mark.asyncio
async def test_client_rejects_double_connect():
    bc = DummyBC()
    client = DanmakuClient(Config(), bc)
    import asyncio

    async def never():
        await asyncio.sleep(3600)

    client.task = asyncio.create_task(never())
    with pytest.raises(ValueError):
        await client.connect("123", mock=True)
    client.task.cancel()


def test_client_bad_input_raises():
    bc = DummyBC()

    async def check():
        client = DanmakuClient(Config(), bc)
        with pytest.raises(ValueError):
            await client.connect("这不是链接")

    __import__("asyncio").run(check())
