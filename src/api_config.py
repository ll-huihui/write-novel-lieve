"""配置中心与语音库路由（T9）：读改配置、音色列表/试听、角色绑定、书籍归档。

拆出自 api.py（单文件 ≤400 行规范）。app_state 与 getter 复用 api.py 的。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ValidationError

from .api import get_broadcaster, get_chat_host, get_config, get_engine
from .config import PROJECT_ROOT, masked_config, save_config
from .llm import PRESETS
from .tts import OmniVoiceTTS, scan_voices
from .voice_scheduler import VoiceScheduler

router = APIRouter(prefix="/api")


# ---- 配置读写 ----

@router.get("/config")
async def get_full_config() -> dict:
    return {"ok": True, "config": masked_config(get_config()),
            "builtin_presets": sorted(PRESETS.keys())}


@router.post("/config")
async def update_full_config(req: "ConfigUpdateRequest") -> dict:
    """保存配置并热应用：原地更新共享 Config 对象（各组件引用不变，即时生效）。

    api_key 传回 bool（未修改）时保留旧值；TTS enabled/url/voices_dir 变化时重建调度器。
    """
    old = get_config()
    data = _restore_masked_keys(req.config, old)  # bool 占位须在「校验前」还原，否则校验直接拒绝
    try:
        new = old.model_validate(data)
    except ValidationError as e:
        return {"ok": False, "error": f"配置校验失败：{e.errors()[0].get('msg', e)}"}
    # 热应用（字段赋值，对象引用不变）
    tts_changed = (old.tts.enabled, old.tts.url, old.tts.voices_dir) != \
                  (new.tts.enabled, new.tts.url, new.tts.voices_dir)
    old.llm, old.writing = new.llm, new.writing
    old.danmaku, old.interaction, old.tts = new.danmaku, new.interaction, new.tts
    save_config(old)
    notes = []
    if tts_changed:
        notes.append(_rebuild_voice(old))
    return {"ok": True, "notes": notes}


def _restore_masked_keys(data: dict, old) -> dict:
    """前端回传的 api_key 为 bool（脱敏占位：True=已配置 False=未配置）→ 还原为旧值。"""
    presets = data.get("llm", {}).get("presets", {})
    for name, preset in presets.items():
        if isinstance(preset.get("api_key"), bool):
            old_preset = old.llm.presets.get(name)
            preset["api_key"] = old_preset.api_key if old_preset else ""
    for name in ("writing", "review", "chat"):
        prof = data.get("llm", {}).get(name)
        if prof and isinstance(prof.get("api_key"), bool):
            prof["api_key"] = getattr(old.llm, name).api_key
    return data


class ConfigUpdateRequest(BaseModel):
    config: dict


def _rebuild_voice(config) -> str:
    """按新 tts 配置重建/摘除语音调度器，挂回 ChatHost。"""
    host = get_chat_host()
    if host is None:
        return ""
    if config.tts.enabled:
        voices_dir = config.tts.voices_dir or str(PROJECT_ROOT / "assets" / "voices")
        host.voice_scheduler = VoiceScheduler(
            OmniVoiceTTS(config.tts.url), get_broadcaster(), voices_dir)
        return "语音已开启"
    host.voice_scheduler = None
    return "语音已关闭"


# ---- 音色库 ----

@router.get("/voices")
async def list_voices() -> dict:
    config = get_config()
    voices_dir = config.tts.voices_dir or str(PROJECT_ROOT / "assets" / "voices")
    voices = [{"name": v["name"], "label": v["label"], "kind": v["kind"]}
              for v in scan_voices(voices_dir).values()]
    host = get_chat_host()
    scheduler = host.voice_scheduler if host else None
    return {"ok": True, "voices": voices,
            "scheduler": scheduler.status_payload() if scheduler else None,
            "enabled": config.tts.enabled}


class VoicePreviewRequest(BaseModel):
    voice: str


@router.post("/voice/preview")
async def voice_preview(req: VoicePreviewRequest) -> dict:
    scheduler = (get_chat_host().voice_scheduler if get_chat_host() else None)
    if scheduler is None:
        return {"ok": False, "error": "TTS 未开启（先在语音配置里打开）"}
    ok = scheduler.enqueue("你好，我是本直播间的人工智能作者，这是音色试听。", req.voice)
    if not ok:
        return {"ok": False, "error": "语音队列已满，稍后再试"}
    return {"ok": True}


# ---- 角色-音色绑定 ----

class VoiceBindRequest(BaseModel):
    bindings: list[dict]  # [{name, voice}]


@router.post("/novel/{novel_id}/voices")
async def bind_voices(novel_id: str, req: VoiceBindRequest) -> dict:
    engine = get_engine()
    try:
        n = engine.bind_voices(novel_id, req.bindings)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "bound": n}


# ---- 书籍归档 ----

@router.post("/novel/{novel_id}/archive")
async def archive_novel(novel_id: str) -> dict:
    engine = get_engine()
    try:
        return {"ok": True, **engine.archive(novel_id)}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
