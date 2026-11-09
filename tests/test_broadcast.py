"""广播快照与写作循环纯逻辑单测（T4）。"""

import pytest

from src.broadcaster import state_payload
from src.config import Config
from src.live_loop import WritingLoop
from src.novel_store import NovelModel, NovelStore
from src.writer import STATE_IDLE, STATE_WRITING, WritingEngine


def make_engine(tmp_path) -> tuple[WritingEngine, NovelModel]:
    config = Config()
    store = NovelStore(root=tmp_path)
    novel = store.create(NovelModel(id="t4", title="直播测试书", status="writing"))
    engine = WritingEngine(config, store)
    engine.current_novel_id = novel.id
    return engine, novel


class DummyBroadcaster:
    def __init__(self):
        self.msgs = []

    async def broadcast(self, msg):
        self.msgs.append(msg)

    async def broadcast_state(self, engine, loop=None):
        self.msgs.append({"type": "state"})


def test_state_payload_fields(tmp_path):
    engine, novel = make_engine(tmp_path)
    engine.state = STATE_WRITING
    info = state_payload(engine)
    assert info["state"] == "writing"
    assert info["title"] == "直播测试书"
    assert info["target_words"] == Config().writing.target_words
    assert info["total_words"] == 0
    assert info["chapter_index"] == 0


def test_snapshot_contains_chapter_index(tmp_path):
    engine, novel = make_engine(tmp_path)
    engine.store.add_paragraph(novel, "第一段内容" * 10, "钩子。")
    bc_snapshot = __import__("src.broadcaster", fromlist=["StageBroadcaster"]).StageBroadcaster()
    snap = bc_snapshot.snapshot(engine)
    assert snap["type"] == "sync"
    assert snap["chapters"][0]["index"] == 1
    assert snap["chapters"][0]["paragraphs"][0]["text"].startswith("第一段内容")
    assert snap["total_words"] > 0


def test_snapshot_empty_when_no_novel(tmp_path):
    engine = WritingEngine(Config(), NovelStore(root=tmp_path))
    engine.current_novel_id = None
    snap = __import__("src.broadcaster", fromlist=["StageBroadcaster"]).StageBroadcaster().snapshot(engine)
    assert snap["chapters"] == [] and snap["state"] == "idle"


@pytest.mark.asyncio
async def test_loop_start_refusals(tmp_path):
    engine, novel = make_engine(tmp_path)
    loop = WritingLoop(engine, DummyBroadcaster(), Config())
    await loop.start("t4")
    with pytest.raises(ValueError):
        await loop.start("t4")  # 已在运行
    loop.running = False
    # 已完结的书不能开写
    novel.status = "finished"
    engine.store.save(novel)
    with pytest.raises(ValueError):
        await loop.start("t4")


def test_loop_speed_clamp(tmp_path):
    loop = WritingLoop(WritingEngine(Config(), NovelStore(root=tmp_path)), DummyBroadcaster(), Config())
    loop.set_speed(5)
    assert loop.speed_ms == 20
    loop.set_speed(250)
    assert loop.speed_ms == 250


@pytest.mark.asyncio
async def test_loop_survives_paragraph_failures(tmp_path):
    """单段生成失败只警告重试，连续 3 次才退出循环（T4 联调回归）。"""
    import asyncio

    engine, novel = make_engine(tmp_path)
    engine.store.add_paragraph(novel, "第一段内容" * 10, "钩子。")  # 跳过开头生成
    calls = {"n": 0}

    async def boom(_novel_id, **kwargs):
        calls["n"] += 1
        raise ValueError("段落两次生成均未通过规则校验")

    engine.continue_paragraph = boom
    bc = DummyBroadcaster()
    loop = WritingLoop(engine, bc, Config())
    loop.retry_delay = 0
    await loop.start("t4")
    while loop.running:  # 循环应在第 3 次失败后自行退出
        await asyncio.sleep(0.02)
    await loop._task
    assert calls["n"] == 3
    notices = [m for m in bc.msgs if m.get("notice")]
    errors = [m for m in bc.msgs if m.get("error")]
    assert len(notices) == 3 and len(errors) == 1
