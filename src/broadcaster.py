"""stage 页 WebSocket 广播：连接管理、四类消息推送、断线重连全量快照。"""
from __future__ import annotations

from fastapi import WebSocket

from .novel_store import NovelModel
from .writer import WritingEngine


def state_payload(engine: WritingEngine, loop=None) -> dict:
    """当前状态汇总：状态机状态 + 章节位置 + 字数进度（state 消息正文）。"""
    novel: NovelModel | None = None
    if engine.current_novel_id:
        try:
            novel = engine.store.get(engine.current_novel_id)
        except ValueError:
            novel = None
    chapter = novel.chapters[-1] if novel and novel.chapters else None
    return {
        "state": engine.state,
        "novel_id": engine.current_novel_id,
        "title": novel.title if novel else "",
        "novel_status": novel.status if novel else "",
        "chapter_index": chapter.index if chapter else 0,
        "chapter_title": chapter.title if chapter else "",
        "chapter_count": len(novel.chapters) if novel else 0,
        "total_words": engine.store.total_words(novel) if novel else 0,
        "target_words": novel.target_words if novel else 0,
        "target_chapters": novel.target_chapters if novel else 0,
        "gap_remaining": loop.gap_remaining if loop else None,
    }


class StageBroadcaster:
    def __init__(self):
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, msg: dict) -> None:
        dead = []
        for ws in self._clients:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    async def broadcast_state(self, engine: WritingEngine, loop=None) -> None:
        await self.broadcast({"type": "state", **state_payload(engine, loop)})

    def snapshot(self, engine: WritingEngine, loop=None) -> dict:
        """断线重连全量快照：状态 + 全书章节索引与正文（含章节锚点）。"""
        novel = None
        if engine.current_novel_id:
            try:
                novel = engine.store.get(engine.current_novel_id)
            except ValueError:
                novel = None
        chapters = []
        if novel:
            for c in novel.chapters:
                chapters.append({
                    "index": c.index,
                    "title": c.title,
                    "summary": c.summary,
                    "paragraphs": [{"index": i, "text": p.text}
                                   for i, p in enumerate(c.paragraphs, 1)],
                })
        return {"type": "sync", **state_payload(engine, loop), "chapters": chapters}
