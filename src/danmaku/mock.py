"""mock 弹幕流：无直播环境演示/联调用。

产出的是「douyinLive 原始格式」消息（和真实服务一致的形状），
走同一条 normalize 通路，顺便覆盖连击去重、system 忽略等分支。
"""
from __future__ import annotations

import asyncio
import json

_SAMPLES = [
    {"method": "WebcastChatMessage", "user": {"nickname": "追更的猫"},
     "content": "这个开头有点东西！"},
    {"method": "WebcastChatMessage", "user": {"nickname": "夜航星"},
     "content": "洛薇千万别碰那个祭坛……"},
    {"method": "WebcastMemberMessage", "user": {"nickname": "路人甲"},
     "content": "来了来了"},
    {"method": "WebcastGiftMessage", "user": {"nickname": "书友_9527"},
     "gift": {"name": "小心心", "diamond_count": 1}, "repeatCount": 1},
    {"method": "WebcastChatMessage", "user": {"nickname": "非常长的昵称测试一二三四五六七八九十"},
     "content": "  主角快跑�\x07  "},
    # 连击中间帧：应被去重跳过
    {"method": "WebcastGiftMessage", "user": {"nickname": "大佬袁"},
     "gift": {"name": "玫瑰", "diamond_count": 1}, "repeatCount": 3, "groupId": "g1"},
    {"method": "WebcastChatMessage", "user": {"nickname": "拼图的人"},
     "content": "第三章标题好带感"},
    # 连击结束帧：应计入（数量取 repeatCount）
    {"method": "WebcastGiftMessage", "user": {"nickname": "大佬袁"},
     "gift": {"name": "玫瑰", "diamond_count": 1}, "repeatCount": 3,
     "groupId": "g1", "repeatEnd": 1},
    {"method": "WebcastChatMessage", "user": {"nickname": "阿澜"},
     "content": "主播写得比起点某些签约作者稳"},
    # 投票弹幕（投票开启时计入票数，不触发闲聊）
    {"method": "WebcastChatMessage", "user": {"nickname": "追更的猫"}, "content": "1"},
    {"method": "WebcastChatMessage", "user": {"nickname": "夜航星"}, "content": "2"},
    {"method": "WebcastChatMessage", "user": {"nickname": "书友_9527"}, "content": "1"},
    {"type": "system", "code": 0, "message": "heartbeat"},
]


async def mock_stream(interval: float = 3.0):
    """循环 yield 样例原始消息 JSON 字符串，模拟 douyinLive 推流。"""
    i = 0
    while True:
        yield json.dumps(_SAMPLES[i % len(_SAMPLES)], ensure_ascii=False)
        i += 1
        await asyncio.sleep(interval)
