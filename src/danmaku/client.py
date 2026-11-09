"""douyinLive WS 客户端：连接、断线重连、消息分发（IO 层）。

- 房间号由 resolve_room 解析（支持链接/分享文本）
- 断线指数退避重连（3s → 30s 封顶），直到主动 disconnect
- mock 模式走本地样例流，与真实消息同一条归一化通路
"""
from __future__ import annotations

import asyncio
import json

import websockets

from ..broadcaster import StageBroadcaster
from ..config import Config
from .mock import mock_stream
from .normalize import normalize
from .resolve import resolve_room


class DanmakuClient:
    def __init__(self, config: Config, broadcaster: StageBroadcaster):
        self.config = config
        self.broadcaster = broadcaster
        self.task: asyncio.Task | None = None
        self.room = ""
        self.mock = False
        self.status = "disconnected"  # disconnected/connecting/connected/error
        self.error = ""  # 最近一次错误原因（供前端展示）
        self.on_danmaku = None  # 归一化弹幕回调（main 注入 ChatHost 等）

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def status_payload(self) -> dict:
        return {"status": self.status, "room": self.room, "mock": self.mock, "error": self.error}

    async def connect(self, room_input: str, mock: bool = False) -> dict:
        if self.running:
            raise ValueError("弹幕已连接，请先断开")
        if mock:
            self.room, self.mock = "", True
        else:
            room = await resolve_room(room_input)
            if not room:
                raise ValueError("房间号不能为空")
            self.room, self.mock = room, False
        self.status, self.error = "connecting", ""
        self.task = asyncio.create_task(self._run())
        return self.status_payload()

    async def disconnect(self) -> dict:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        self.room, self.mock, self.status, self.error = "", False, "disconnected", ""
        await self._push_status()
        return self.status_payload()

    async def _push_status(self) -> None:
        await self.broadcaster.broadcast({"type": "danmaku_status", **self.status_payload()})

    async def _handle(self, raw: str) -> None:
        try:
            obj = json.loads(raw)
        except ValueError:
            return
        msg = normalize(obj)
        if msg:
            await self.broadcaster.broadcast({"type": "danmaku", **msg})
            if self.on_danmaku:
                await self.on_danmaku(msg)

    async def _run(self) -> None:
        backoff = 3
        try:
            if self.mock:
                self.status = "connected"
                await self._push_status()
                async for raw in mock_stream():
                    await self._handle(raw)
                return
            url = f"{self.config.danmaku.douyin_ws_url.rstrip('/')}/ws/{self.room}"
            while True:  # 断线自动重连
                try:
                    async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                        self.status, self.error = "connected", ""
                        await self._push_status()
                        async for raw in ws:
                            await self._handle(raw)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.status, self.error = "error", str(e)[:120]
                    await self._push_status()
                await asyncio.sleep(backoff)
                backoff = min(backoff + 3, 30)
                self.status = "connecting"
        except asyncio.CancelledError:
            pass
        finally:
            if not self.running:
                self.status = "disconnected"
