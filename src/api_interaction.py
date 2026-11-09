"""互动剧情路由（T8）：投票发起/关闭/状态、AI 出题。

拆自 api.py（单文件 ≤400 行规范）；app_state 复用 api.py 的。
"""
from fastapi import APIRouter
from pydantic import BaseModel

from .api import app_state, get_interaction

router = APIRouter(prefix="/api")


class VoteStartRequest(BaseModel):
    question: str
    options: list[str]
    duration: int = 0  # 0 = 用配置的 vote_duration


@router.post("/vote/start")
async def vote_start(req: VoteStartRequest) -> dict:
    return await get_interaction().open_vote(req.question, req.options,
                                             req.duration or None)


@router.post("/vote/close")
async def vote_close() -> dict:
    return await get_interaction().close_vote()


@router.post("/vote/generate")
async def vote_generate() -> dict:
    """AI 据当前剧情出题（只返回候选，主播可改后用 /vote/start 发起）。"""
    try:
        return await get_interaction().generate_question()
    except Exception as e:  # LLM 不可用等
        return {"ok": False, "error": str(e)[:120]}


@router.get("/vote/state")
async def vote_state() -> dict:
    manager = app_state["interaction"]
    return {"ok": True, "vote": manager.vote_payload(), "pool": manager.pool.digest()}
