"""API 路由：健康检查、LLM 测试、小说全生命周期、直播页 WS、直播控制（T4）。"""

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .broadcaster import StageBroadcaster, state_payload
from .config import Config
from .llm import LLMError, stream_chat, strip_think
from .live_loop import WritingLoop
from .writer import STATE_PAUSED, WritingEngine

router = APIRouter(prefix="/api")

app_state: dict = {}


def set_config(config: Config) -> None:
    app_state["config"] = config


def get_config() -> Config:
    return app_state["config"]


def set_engine(engine: WritingEngine) -> None:
    app_state["engine"] = engine


def get_engine() -> WritingEngine:
    return app_state["engine"]


def set_broadcaster(bc: StageBroadcaster) -> None:
    app_state["broadcaster"] = bc


def get_broadcaster() -> StageBroadcaster:
    return app_state["broadcaster"]


def set_loop(loop: WritingLoop) -> None:
    app_state["loop"] = loop


def get_loop() -> WritingLoop:
    return app_state["loop"]


def set_interaction(manager) -> None:
    app_state["interaction"] = manager


def get_interaction():
    return app_state["interaction"]


def set_danmaku(client) -> None:
    app_state["danmaku"] = client


def get_danmaku():
    return app_state["danmaku"]


def set_chat_host(host) -> None:
    app_state["chat_host"] = host


def get_chat_host():
    return app_state.get("chat_host")


# ---- 弹幕接入（T5） ----

class DanmakuConnectRequest(BaseModel):
    room: str = ""    # 房间号 / 直播间链接 / 分享口令文本
    mock: bool = False


@router.post("/live/danmaku/connect")
async def danmaku_connect(req: DanmakuConnectRequest) -> dict:
    try:
        return {"ok": True, **await get_danmaku().connect(req.room, req.mock)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/live/danmaku/disconnect")
async def danmaku_disconnect() -> dict:
    return {"ok": True, **await get_danmaku().disconnect()}


@router.get("/live/danmaku/status")
async def danmaku_status() -> dict:
    return {"ok": True, **get_danmaku().status_payload()}


@router.get("/health")
async def health() -> dict:
    config: Config | None = app_state.get("config")
    engine: WritingEngine | None = app_state.get("engine")
    return {
        "status": "ok",
        "service": "write-novel-live",
        "server_port": config.server.port if config else None,
        "writing_preset": config.llm.writing.preset if config else None,
        "writing_model": config.llm.writing.model if config else None,
        "state": engine.state if engine else None,
        "current_novel_id": engine.current_novel_id if engine else None,
    }


# ---- LLM 测试（控制台"测试连接"后端） ----

class LLMTestRequest(BaseModel):
    provider: str = "writing"  # writing | chat | review
    prompt: str


@router.post("/llm/test")
async def llm_test(req: LLMTestRequest) -> dict:
    config = get_config()
    provider = req.provider if req.provider in ("writing", "chat", "review") else "writing"
    profile = getattr(config.llm, provider)
    if provider != "writing" and not profile.preset:
        profile = config.llm.writing
    messages = [{"role": "user", "content": req.prompt}]
    parts: list[str] = []
    try:
        async for delta in stream_chat(
            profile, messages, custom_presets={k: v.model_dump() for k, v in config.llm.presets.items()}
        ):
            parts.append(delta)
    except LLMError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "text": strip_think("".join(parts))}


# ---- 小说生命周期 ----

def _err(e: Exception) -> dict:
    return {"ok": False, "error": str(e)}


@router.get("/novels")
async def list_novels() -> dict:
    engine = get_engine()
    novels = []
    for nid in engine.store.list_ids():
        n = engine.store.get(nid)
        novels.append({"id": n.id, "title": n.title, "genre": n.genre, "status": n.status,
                       "total_words": engine.store.total_words(n), "chapters": len(n.chapters)})
    return {"ok": True, "novels": novels}


class NovelGenRequest(BaseModel):
    genre_hint: str = ""   # 设定卡：题材方向（可空=AI 自定）
    idea: str = ""         # 设定卡：核心创意提示（可空）
    target_words: int = 0  # 0 = 用全局配置
    target_chapters: int = 0


@router.post("/novel/generate")
async def generate_novel(req: NovelGenRequest | None = None) -> dict:
    req = req or NovelGenRequest()
    try:
        novel = await get_engine().generate_novel(
            genre_hint=req.genre_hint, idea=req.idea,
            target_words=req.target_words, target_chapters=req.target_chapters)
    except (LLMError, ValueError) as e:
        return _err(e)
    return {"ok": True, "novel": novel.model_dump()}


@router.get("/novel/{novel_id}")
async def get_novel(novel_id: str) -> dict:
    engine = get_engine()
    try:
        novel = engine.store.get(novel_id)
    except ValueError as e:
        return _err(e)
    return {"ok": True, "novel": novel.model_dump(),
            "total_words": engine.store.total_words(novel)}


@router.post("/novel/{novel_id}/open")
async def open_novel(novel_id: str) -> dict:
    try:
        return {"ok": True, **await get_engine().generate_opening(novel_id)}
    except (LLMError, ValueError) as e:
        return _err(e)


@router.post("/novel/{novel_id}/continue")
async def continue_novel(novel_id: str) -> dict:
    try:
        return {"ok": True, **await get_engine().continue_paragraph(novel_id)}
    except (LLMError, ValueError) as e:
        return _err(e)


@router.post("/novel/{novel_id}/chapter-end")
async def chapter_end(novel_id: str) -> dict:
    try:
        return {"ok": True, **await get_engine().chapter_end(novel_id)}
    except (LLMError, ValueError) as e:
        return _err(e)


@router.post("/novel/{novel_id}/ending")
async def ending_novel(novel_id: str, action: str = "start") -> dict:
    try:
        engine = get_engine()
        if action == "finish":
            return {"ok": True, **await engine.finish(novel_id)}
        return {"ok": True, **await engine.start_ending(novel_id)}
    except (LLMError, ValueError) as e:
        return _err(e)


@router.post("/novel/{novel_id}/pause")
async def pause_novel(novel_id: str) -> dict:
    result = {"ok": True, **get_engine().pause()}
    await get_broadcaster().broadcast_state(get_engine(), get_loop())
    return result


@router.post("/novel/{novel_id}/resume")
async def resume_novel(novel_id: str) -> dict:
    result = {"ok": True, **get_engine().resume()}
    await get_broadcaster().broadcast_state(get_engine(), get_loop())
    return result


class EditRequest(BaseModel):
    chapter_index: int
    para_index: int
    text: str


@router.post("/novel/{novel_id}/edit")
async def edit_paragraph(novel_id: str, req: EditRequest) -> dict:
    """人工编辑段落：仅暂停状态可保存；保存后自动恢复之前状态。"""
    engine = get_engine()
    if engine.state != STATE_PAUSED:
        return {"ok": False, "error": "请先暂停再编辑"}
    try:
        novel = engine.store.get(novel_id)
        chapter = next(c for c in novel.chapters if c.index == req.chapter_index)
        para = chapter.paragraphs[req.para_index - 1]
        engine.store.replace_paragraph(novel, req.chapter_index, req.para_index,
                                       req.text.strip(), para.hook, edited=True)
    except (ValueError, IndexError, StopIteration) as e:
        return {"ok": False, "error": f"编辑失败：{e}"}
    engine.resume()
    await get_broadcaster().broadcast_state(engine, get_loop())
    return {"ok": True, "state": engine.state}


# ---- 直播页 WS 与直播控制 ----

@router.websocket("/ws/stage")
async def ws_stage(ws: WebSocket) -> None:
    bc = get_broadcaster()
    await bc.connect(ws)
    try:
        snap = bc.snapshot(get_engine(), get_loop())
        interaction = app_state.get("interaction")
        if interaction:
            snap["vote"] = interaction.vote_payload()  # 重连恢复进行中的投票卡片
        host = get_chat_host()
        if host:
            snap["chat_bubbles"] = list(host.recent_bubbles)  # 最近气泡不因刷新丢失
        await ws.send_json(snap)
        # 弹幕连接状态是"变化才广播"的，新接入的页面单独补发一次
        await ws.send_json({"type": "danmaku_status", **get_danmaku().status_payload()})
        while True:
            await ws.receive_text()  # 客户端消息仅作保活
    except WebSocketDisconnect:
        pass
    finally:
        bc.disconnect(ws)


@router.post("/live/start/{novel_id}")
async def live_start(novel_id: str) -> dict:
    try:
        await get_loop().start(novel_id)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    await get_broadcaster().broadcast_state(get_engine(), get_loop())
    return {"ok": True, "state": get_engine().state}


@router.post("/live/stop")
async def live_stop() -> dict:
    await get_loop().stop()
    return {"ok": True, "running": False}


@router.post("/live/skip")
async def live_skip() -> dict:
    get_loop().skip()
    await get_broadcaster().broadcast({"type": "skip"})
    return {"ok": True}


@router.post("/live/pause")
async def live_pause() -> dict:
    """直播页快捷暂停：作用于当前直播书（与 /novel/{id}/pause 同逻辑）。"""
    engine = get_engine()
    if not engine.current_novel_id:
        return {"ok": False, "error": "当前没有直播中的书"}
    result = {"ok": True, **engine.pause()}
    await get_broadcaster().broadcast_state(engine, get_loop())
    return result


@router.post("/live/resume")
async def live_resume() -> dict:
    engine = get_engine()
    if not engine.current_novel_id:
        return {"ok": False, "error": "当前没有直播中的书"}
    result = {"ok": True, **engine.resume()}
    await get_broadcaster().broadcast_state(engine, get_loop())
    return result


class SpeedRequest(BaseModel):
    speed_ms: int


@router.post("/live/speed")
async def live_speed(req: SpeedRequest) -> dict:
    get_loop().set_speed(req.speed_ms)
    return {"ok": True, "speed_ms": get_loop().speed_ms}


@router.get("/live/state")
async def live_state() -> dict:
    loop = get_loop()
    return {"ok": True, "running": loop.running, "speed_ms": loop.speed_ms,
            **state_payload(get_engine(), loop)}
