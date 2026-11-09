"""全局语音互斥调度器（T7）：同一时刻只有一个语音在合成/推送。

设计：
- 单 worker 消费有界队列（结构性保证互斥），闲聊语音时效性强 → 队列满(3)丢新条目
- 推送间隔 ≥ 上一条音频时长 + 缓冲（浏览器串行播放不积压、不重叠）
- priority 预留：后续正文朗读/系统提示插队用（当前所有条目同优先级，按入队顺序）
- TTS 失败/不可达：丢弃该条并计数，绝不阻塞写作与闲聊主链路
- 推送载体：WS {"type":"voice","audio":<base64 mp3>,"duration_ms":...,"text":...}
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time

from .tts import OmniVoiceTTS, TTSError, audio, scan_voices

log = logging.getLogger("voice")

QUEUE_MAX = 3
PLAY_GAP_MS = 400  # 两条语音之间的缓冲


class VoiceItem:
    __slots__ = ("text", "voice", "priority", "seq")

    def __init__(self, text: str, voice: str, priority: int, seq: int):
        self.text = text
        self.voice = voice
        self.priority = priority
        self.seq = seq


class VoiceScheduler:
    def __init__(self, tts: OmniVoiceTTS, broadcaster, voices_dir: str):
        self.tts = tts
        self.broadcaster = broadcaster
        self.voices: dict[str, dict] = scan_voices(voices_dir)
        self._queue: asyncio.Queue[VoiceItem] = asyncio.Queue(maxsize=QUEUE_MAX)
        self._worker: asyncio.Task | None = None
        self._seq = 0
        self.dropped = 0       # 队列满被丢条数
        self.failed = 0        # 合成失败条数
        self.last_played: dict = {}  # 最近一条推送信息（诊断用）

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def enqueue(self, text: str, voice: str, priority: int = 0) -> bool:
        """入队并确保 worker 运行。队列满丢新条目（闲聊语音过期无效）。"""
        if not text.strip():
            return False
        self._seq += 1
        try:
            self._queue.put_nowait(VoiceItem(text.strip(), voice, priority, self._seq))
        except asyncio.QueueFull:
            self.dropped += 1
            return False
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())
        return True

    async def _run(self) -> None:
        while not self._queue.empty():
            item = await self._queue.get()
            t0 = time.monotonic()
            try:
                preset = self.voices.get(item.voice) or next(iter(self.voices.values()), None)
                if preset is None:
                    raise TTSError("音色库为空")
                mp3 = await self.tts.synth_mp3(item.text, preset)
                if not mp3:
                    continue
                pcm = await asyncio.get_running_loop().run_in_executor(
                    None, audio.transcode, None, mp3, "pcm")
                duration_ms = int(audio.pcm_seconds(pcm) * 1000)
                await self.broadcaster.broadcast({
                    "type": "voice",
                    "audio": base64.b64encode(mp3).decode(),
                    "duration_ms": duration_ms,
                    "text": item.text[:80],
                    "voice": item.voice,
                })
                self.last_played = {"text": item.text[:80], "duration_ms": duration_ms,
                                    "at": time.time()}
                # 推送间隔 ≥ 音频时长 + 缓冲：浏览器串行播完再给下一条
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                await asyncio.sleep(max(0, duration_ms + PLAY_GAP_MS - elapsed_ms) / 1000)
            except TTSError as e:
                self.failed += 1
                log.warning("语音合成失败(丢弃): %s", e)
            except Exception:  # noqa: BLE001 调度器绝不整体崩溃
                self.failed += 1
                log.exception("语音调度异常(丢弃)")

    def status_payload(self) -> dict:
        return {"pending": self.pending, "dropped": self.dropped, "failed": self.failed,
                "voices": len(self.voices), "tts_base": self.tts.active_base()}
