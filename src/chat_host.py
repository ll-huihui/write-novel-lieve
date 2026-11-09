"""AI 主播闲聊：独立 LLM 历史、节流回复、绝不进正文（T6）。

职责边界（结构性保证闲聊不污染正文）：
- 本模块不持有 novel_store 的写入口，只读当前进度做语境，只广播 chat_bubble
- 写作上下文组装（writer/prompts.context_user）不读本模块历史
- 弹幕先过 filter.is_blocked，命中整条丢弃（不复读、不喂 LLM）

回复策略：节流窗口（interaction.chat_interval 秒）内只回最新一条；
生成期间新弹幕进 pending 队列，完成后取最新继续；同一时刻只有一个生成。
"""
from __future__ import annotations

import asyncio
import time
from collections import deque

from .broadcaster import StageBroadcaster
from .config import Config
from .danmaku.filter import is_blocked
from .llm import LLMError, stream_chat, strip_think
from .novel_store import NovelModel
from .writer.prompts import SYSTEM_HOST_TMPL, host_user

MAX_REPLY_CHARS = 120  # 兜底截断（prompt 已约束 60 字）


class ChatHost:
    def __init__(self, config: Config, broadcaster: StageBroadcaster,
                 get_novel: "callable | None" = None, voice_scheduler=None):
        self.config = config
        self.broadcaster = broadcaster
        self.get_novel = get_novel  # () -> NovelModel | None，由 main 注入
        self.voice_scheduler = voice_scheduler  # T7：闲聊语音互斥调度（tts.enabled 时注入）
        self.history: deque = deque(maxlen=20)   # 独立闲聊历史（10 轮）
        self.recent_bubbles: deque = deque(maxlen=5)  # 快照用，刷新页面气泡不丢
        self.pending: deque = deque(maxlen=10)   # 等待回复的弹幕（只留最新）
        self.busy = False
        self._last_reply_at = 0.0

    # ---- 入口：DanmakuClient.on_danmaku 回调 ----

    async def handle_danmaku(self, msg: dict) -> None:
        if not self.config.interaction.chat_enabled:
            return
        if msg.get("kind") != "chat":
            return
        if is_blocked(msg.get("content", "")):
            return
        self.pending.append(msg)
        if not self.busy:
            asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        interval = max(1, self.config.interaction.chat_interval)
        self.busy = True
        try:
            while self.pending:
                waited = time.monotonic() - self._last_reply_at
                if self._last_reply_at and waited < interval:
                    await asyncio.sleep(interval - waited)
                if not self.pending:
                    break
                msg = self.pending.pop()      # 节流窗口内只回最新一条，
                self.pending.clear()          # 积压的旧弹幕直接略过（闲聊有时效性）
                self._last_reply_at = time.monotonic()
                await self._reply(msg)
        finally:
            self.busy = False

    # ---- 生成与广播 ----

    def _system_prompt(self, novel: NovelModel | None) -> str:
        return SYSTEM_HOST_TMPL.format(
            title=novel.title if novel else "未命名",
            genre=novel.genre if novel else "未知",
            chapter=len(novel.chapters) if novel else 0,
        )

    def _messages(self, msg: dict) -> list[dict]:
        novel = self.get_novel() if self.get_novel else None
        return (
            [{"role": "system", "content": self._system_prompt(novel)}]
            + list(self.history)
            + [{"role": "user", "content": host_user(msg.get("nick", "观众"),
                                                     msg.get("content", ""))}]
        )

    async def _reply(self, msg: dict) -> None:
        profile = self.config.llm.chat
        if not profile.preset:
            profile = self.config.llm.writing  # chat 档位未配预设时回退写作模型
        parts: list[str] = []
        try:
            async for delta in stream_chat(
                profile, self._messages(msg),
                custom_presets={k: v.model_dump() for k, v in self.config.llm.presets.items()},
            ):
                parts.append(delta)
        except LLMError:
            return  # 闲聊失败静默跳过，不影响写作
        reply = strip_think("".join(parts)).replace("\n", " ").strip()[:MAX_REPLY_CHARS]
        if not reply:
            return
        user_content = f"{msg.get('nick', '观众')}：{msg.get('content', '')}"
        self.history.append({"role": "user", "content": user_content})
        self.history.append({"role": "assistant", "content": reply})
        self.recent_bubbles.append({"nick": msg.get("nick", "观众"), "reply": reply})
        await self.broadcaster.broadcast({
            "type": "chat_bubble", "nick": msg.get("nick", "观众"), "reply": reply})
        if self.voice_scheduler:
            self.voice_scheduler.enqueue(reply, self.config.tts.voice)
