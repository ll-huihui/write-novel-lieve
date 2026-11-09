"""write-novel-live 入口：python -m src.main 启动 FastAPI 服务（默认 :8800）。"""

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import (router, set_broadcaster, set_chat_host, set_config,
                  set_danmaku, set_engine, set_interaction, set_loop)
from .api_config import router as config_router
from .api_interaction import router as interaction_router
from .broadcaster import StageBroadcaster
from .chat_host import ChatHost
from .config import load_config
from .danmaku import DanmakuClient
from .interaction import InteractionManager
from .live_loop import WritingLoop
from .novel_store import NovelStore
from .tts import OmniVoiceTTS
from .voice_scheduler import VoiceScheduler
from .writer import WritingEngine

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def create_app() -> FastAPI:
    config = load_config()
    app = FastAPI(title="write-novel-live", version="0.1.0")
    app.include_router(router)
    app.include_router(config_router)
    app.include_router(interaction_router)
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    set_config(config)
    engine = WritingEngine(config, NovelStore())
    set_engine(engine)
    broadcaster = StageBroadcaster()
    set_broadcaster(broadcaster)
    scheduler = None
    if config.tts.enabled:
        voices_dir = config.tts.voices_dir or str(PROJECT_ROOT / "assets" / "voices")
        scheduler = VoiceScheduler(OmniVoiceTTS(config.tts.url), broadcaster, voices_dir)
    get_novel = (lambda: engine.store.get(engine.current_novel_id)
                 if engine.current_novel_id else None)
    chat_host = ChatHost(config, broadcaster, get_novel=get_novel,
                         voice_scheduler=scheduler)
    set_chat_host(chat_host)
    interaction = InteractionManager(config, broadcaster, get_novel=get_novel)
    set_interaction(interaction)
    set_loop(WritingLoop(engine, broadcaster, config, interaction=interaction))
    danmaku = DanmakuClient(config, broadcaster)
    set_danmaku(danmaku)
    danmaku.on_danmaku = lambda msg: interaction.handle_danmaku(msg, chat_host)

    @app.get("/stage", include_in_schema=False)
    async def stage_page() -> FileResponse:
        return FileResponse(WEB_DIR / "stage.html")

    @app.get("/console", include_in_schema=False)
    async def console_page() -> FileResponse:
        return FileResponse(WEB_DIR / "console.html")

    return app


app = create_app()


def main() -> None:
    config = load_config()
    uvicorn.run(app, host="127.0.0.1", port=config.server.port)


if __name__ == "__main__":
    main()
