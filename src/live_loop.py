"""自动写作循环：写一段 → 广播 → 互动间隙倒计时 → 续写；章满自动章末流转。

打字节奏由前端控制：后端把清洗后的段落分块推送，前端字符队列按字速渲染。
"""
from __future__ import annotations

import asyncio

from .broadcaster import StageBroadcaster
from .config import Config
from .writer import STATE_ENDING, STATE_GAP, STATE_PAUSED, STATE_WRITING, WritingEngine

CHUNK = 200  # 每条 novel_delta 携带的字符数（分块只为减小单帧，节奏仍由前端控制）


class WritingLoop:
    def __init__(self, engine: WritingEngine, broadcaster: StageBroadcaster, config: Config,
                 interaction=None):
        self.engine = engine
        self.broadcaster = broadcaster
        self.config = config
        self.interaction = interaction  # InteractionManager（T8 素材通路，main 注入）
        self.running = False
        self.speed_ms = config.writing.speed_ms
        self.gap_remaining: int | None = None
        self.skip_event = asyncio.Event()
        self.retry_delay = 5  # 单段失败后的重试等待秒数
        self._task: asyncio.Task | None = None

    # ---- 控制 ----

    def set_speed(self, ms: int) -> None:
        self.speed_ms = max(20, int(ms))

    def skip(self) -> None:
        self.skip_event.set()

    async def start(self, novel_id: str) -> None:
        if self.running:
            raise ValueError("写作循环已在运行")
        novel = self.engine.store.get(novel_id)  # 不存在会抛 ValueError
        if novel.status != "writing":
            raise ValueError(f"该书状态为 {novel.status}，不能开写")
        self.engine.current_novel_id = novel_id
        if self.engine.state not in (STATE_WRITING, STATE_ENDING, STATE_PAUSED):
            self.engine.state = STATE_WRITING  # 恢复已有书开写：从写作状态进入循环
        self.running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """请求停止：当前段写完后生效（不打断进行中的 LLM 生成）。"""
        self.running = False

    # ---- 循环体 ----

    async def _run(self) -> None:
        engine = self.engine
        consecutive_failures = 0
        try:
            novel = engine.store.get(engine.current_novel_id)
            if not any(c.paragraphs for c in novel.chapters):
                opening = await engine.generate_opening(engine.current_novel_id)
                await self._push_last_paragraphs(1, len(opening["paragraphs"]))
            while self.running:
                if engine.state == STATE_PAUSED:
                    self.gap_remaining = None
                    await self.broadcaster.broadcast_state(engine, self)
                    await asyncio.sleep(1)
                    continue
                try:  # 单次生成/流转失败不杀循环：警告并重试，连续失败才退出
                    material = self.interaction.pool.digest() if self.interaction else ""
                    result = await engine.continue_paragraph(
                        engine.current_novel_id, audience_material=material)
                    await self._push_paragraph(result)
                    if not self.running:
                        break
                    if result.get("chapter_done"):
                        end = await engine.chapter_end(engine.current_novel_id)
                        await self.broadcaster.broadcast({
                            "type": "chapter_end", "paused": end.get("paused"),
                            "new_chapter_index": end.get("new_chapter_index"),
                            "next_chapter_title": end.get("next_chapter_title"),
                        })
                        if end.get("paused"):
                            continue  # 状态已转为 paused，循环空转等待人工处理
                        if self.interaction and self.config.interaction.auto_vote:
                            asyncio.create_task(self._auto_vote())  # 章末自动出题（不阻塞写作）
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    consecutive_failures += 1
                    notice = f"生成失败将重试（{consecutive_failures}/3）：{str(e)[:120]}"
                    await self.broadcaster.broadcast({"type": "state", "state": engine.state,
                                                      "notice": notice})
                    if consecutive_failures >= 3:
                        raise  # 连续失败转异常退出，由 except 广播错误并复位
                    await asyncio.sleep(self.retry_delay)
                    continue
                consecutive_failures = 0
                if engine.state == STATE_PAUSED:
                    continue
                engine.state = STATE_GAP
                await self._gap_countdown()
                if engine.state == STATE_GAP:
                    engine.state = STATE_WRITING
        except asyncio.CancelledError:
            pass
        except Exception as e:  # 循环异常退出：状态复位并广播错误
            self.running = False
            await self.broadcaster.broadcast({"type": "state", "state": self.engine.state,
                                              "error": str(e)[:200]})
        finally:
            self.gap_remaining = None
            self.running = False

    async def _auto_vote(self) -> None:
        """章末自动出题发起投票：失败静默（投票是锦上添花，不能影响写作循环）。"""
        try:
            result = await self.interaction.auto_generate_and_open()
            if not result.get("ok"):
                await self.broadcaster.broadcast({
                    "type": "state", "state": self.engine.state,
                    "notice": f"自动出题未发起：{result.get('error', '')}"})
        except Exception as e:  # 出题失败不影响写作
            await self.broadcaster.broadcast({
                "type": "state", "state": self.engine.state,
                "notice": f"自动出题失败：{str(e)[:80]}"})

    async def _push_paragraph(self, result: dict) -> None:
        """推送一次生成的全部自然段（模型可能一次输出多段）。"""
        chapter_index = result["chapter_index"]
        end_index = result["para_index"]
        texts = result["paragraphs"]
        for offset, text in enumerate(texts):
            para_index = end_index - len(texts) + offset + 1
            for i in range(0, len(text), CHUNK):
                await self.broadcaster.broadcast({"type": "novel_delta",
                                                  "chapter_index": chapter_index,
                                                  "para_index": para_index,
                                                  "text": text[i:i + CHUNK], "done": False})
            await self.broadcaster.broadcast({"type": "novel_delta",
                                              "chapter_index": chapter_index,
                                              "para_index": para_index, "text": "", "done": True})
        await self.broadcaster.broadcast_state(self.engine, self)

    async def _push_last_paragraphs(self, chapter_index: int, count: int) -> None:
        """开头生成后回放刚生成的全部段落。"""
        novel = self.engine.store.get(self.engine.current_novel_id)
        chapter = next(c for c in novel.chapters if c.index == chapter_index)
        total = len(chapter.paragraphs)
        for para_index in range(total - count, total):
            text = chapter.paragraphs[para_index].text
            for i in range(0, len(text), CHUNK):
                await self.broadcaster.broadcast({"type": "novel_delta",
                                                  "chapter_index": chapter_index,
                                                  "para_index": para_index + 1,
                                                  "text": text[i:i + CHUNK], "done": False})
            await self.broadcaster.broadcast({"type": "novel_delta",
                                              "chapter_index": chapter_index,
                                              "para_index": para_index + 1, "text": "", "done": True})

    async def _gap_countdown(self) -> None:
        gap = self.config.writing.gap_seconds
        self.skip_event.clear()
        for remaining in range(max(1, gap), 0, -1):
            if not self.running or self.skip_event.is_set():
                break
            self.gap_remaining = remaining
            await self.broadcaster.broadcast_state(self.engine, self)
            await asyncio.sleep(1)
        self.gap_remaining = None
