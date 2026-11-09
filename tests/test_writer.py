"""写作引擎单测（T3）：纯逻辑部分（状态机/守卫/审核解析），不调用 LLM。"""

import pytest

from src.config import Config
from src.novel_store import ChapterModel, NovelModel, NovelStore
from src.writer import (
    STATE_ENDING,
    STATE_IDLE,
    STATE_PAUSED,
    STATE_WRITING,
    WritingEngine,
    extract_json_block,
)


def make_engine(tmp_path, target_words=1000, target_chapters=2) -> WritingEngine:
    config = Config()
    config.writing.target_words = target_words
    config.writing.target_chapters = target_chapters
    store = NovelStore(root=tmp_path)
    novel = store.create(NovelModel(id="t1", title="测试", status="writing",
                                    target_words=target_words, target_chapters=target_chapters))
    engine = WritingEngine(config, store)
    engine.current_novel_id = novel.id
    return engine, novel


# ---- 审核 JSON 容错解析 ----

def test_extract_json_block_plain():
    assert extract_json_block('{"pass": true, "issues": []}') == {"pass": True, "issues": []}


def test_extract_json_block_with_noise():
    raw = '好的，审核结果如下：\n{"pass": false, "issues": [{"type": "连贯", "severity": "严重", "detail": "跳跃", "para_index": 2}]}\n请参考。'
    data = extract_json_block(raw)
    assert data["pass"] is False and data["issues"][0]["para_index"] == 2


def test_extract_json_block_with_think():
    raw = '<think>让我看看</think>{"pass": true}'
    assert extract_json_block(raw) == {"pass": True}


def test_extract_json_block_garbage_returns_none():
    assert extract_json_block("完全不是 JSON 的输出") is None
    assert extract_json_block("") is None


# ---- 收官守卫（篇幅保障） ----

def test_ending_check_rejects_when_below_target(tmp_path):
    engine, novel = make_engine(tmp_path, target_words=100000, target_chapters=150)
    engine.store.add_paragraph(novel, "字" * 100, "钩。")
    ok, reason = engine._ending_check(novel)
    assert not ok and "字数" in reason or "章节" in reason


def test_ending_check_allows_when_target_met(tmp_path):
    engine, novel = make_engine(tmp_path, target_words=100, target_chapters=1)
    engine.store.add_paragraph(novel, "字" * 120, "钩。")
    ok, _ = engine._ending_check(novel)
    assert ok


# ---- 状态机转换 ----

def test_pause_resume_roundtrip(tmp_path):
    engine, _ = make_engine(tmp_path)
    engine.state = STATE_WRITING
    assert engine.pause()["state"] == STATE_PAUSED
    assert engine.resume()["state"] == STATE_WRITING


def test_pause_from_ending_returns_to_ending(tmp_path):
    engine, _ = make_engine(tmp_path)
    engine.state = STATE_ENDING
    engine.pause()
    engine.resume()
    assert engine.state == STATE_ENDING


def test_pause_when_paused_is_noop(tmp_path):
    engine, _ = make_engine(tmp_path)
    engine.state = STATE_PAUSED
    engine.pause()
    assert engine.state == STATE_PAUSED


# ---- 章级审核处置流程（喂 mock 的 _review_chapter） ----

@pytest.mark.asyncio
async def test_chapter_end_pauses_on_unresolved_severe(tmp_path, monkeypatch):
    engine, novel = make_engine(tmp_path)
    # 段落必须经 store 持久化（引擎内部会重新从磁盘加载）
    engine.store.add_paragraph(novel, "剧情出现重大逻辑硬伤的段落" * 5, "钩子。")
    engine.state = STATE_WRITING

    async def fake_review(novel, chapter):
        return {"pass": False, "issues": [
            {"type": "逻辑", "severity": "严重", "detail": "时间线错乱", "para_index": 1}]}

    async def fail_rewrite(*a, **k):
        return False  # 重写失败

    monkeypatch.setattr(engine, "_review_chapter", fake_review)
    monkeypatch.setattr(engine, "_rewrite_paragraph", fail_rewrite)
    result = await engine.chapter_end()
    assert result["paused"] is True and engine.state == STATE_PAUSED


@pytest.mark.asyncio
async def test_chapter_end_success_creates_next_chapter(tmp_path, monkeypatch):
    engine, novel = make_engine(tmp_path)
    engine.store.add_paragraph(novel, "正常段落" * 10, "钩子。")
    engine.state = STATE_WRITING

    async def fake_review(novel, chapter):
        return {"pass": True, "issues": []}

    async def fake_summary(novel, chapter):
        return "主角初入宗门。"

    async def fake_propose(novel):
        return "初试锋芒"

    monkeypatch.setattr(engine, "_review_chapter", fake_review)
    monkeypatch.setattr(engine, "_summarize_chapter", fake_summary)
    monkeypatch.setattr(engine, "_propose_next_chapter", fake_propose)
    result = await engine.chapter_end()
    assert result["paused"] is False
    assert result["next_chapter_title"] == "初试锋芒"
    novel = engine.store.get("t1")  # 引擎内部重新落盘，重载后断言
    assert novel.chapters[-1].index == 2
    assert novel.chapters[0].summary == "主角初入宗门。"


# ---- review 档位回退 ----

def test_review_profile_falls_back_to_writing():
    config = Config()
    config.llm.review.preset = ""  # 未配置 review 档位（与 config.example.json 一致）
    engine = WritingEngine(config)
    assert engine._profile("review") is engine.config.llm.writing
    assert engine._profile("writing") is engine.config.llm.writing


# ---- 新书设定分段标记解析 ----

from src.writer.review import parse_novel_setting  # noqa: E402


def test_parse_novel_setting_basic():
    raw = """【书名】夜行档案
【题材】都市悬疑
【世界观】近未来的滨海城市，夜里会出现只有少数人能看见的"第二座城"。
【人物】
- 沈观澜：退役刑警，因旧案失语，性格沉默执拗。
- 白棠：档案管理员，记忆力异常，看似柔弱实则通透。
【大纲】
第一卷（1-30章）：旧案重启，沈观澜卷入连环失踪案。
第二卷（31-70章）：第二座城的秘密逐渐揭开。
第三卷（71-120章）：两座城市的真相与最终对决。"""
    data = parse_novel_setting(raw)
    assert data is not None
    assert data["title"] == "夜行档案"
    assert data["genre"] == "都市悬疑"
    assert len(data["characters"]) == 2
    assert data["characters"][0]["name"] == "沈观澜"
    assert "第二座城" in data["outline"]


def test_parse_novel_setting_with_code_fence_and_think():
    raw = '```text\n【书名】测试书\n【题材】玄幻\n【世界观】测试。\n【人物】\n甲：测试。\n【大纲】测试。\n```'
    assert parse_novel_setting(raw)["title"] == "测试书"


def test_parse_novel_setting_garbage_returns_none():
    assert parse_novel_setting("完全不含标记的内容") is None
    assert parse_novel_setting("") is None
