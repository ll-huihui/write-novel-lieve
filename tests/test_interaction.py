"""互动素材通路单测（T8）：投票状态机、素材池、弹幕计票拦截。均不触 LLM。"""

import pytest

from src.interaction import GIFT_COOLDOWN, InteractionManager, MaterialPool, Vote


class DummyBroadcaster:
    def __init__(self):
        self.msgs = []

    async def broadcast(self, msg):
        self.msgs.append(msg)


def make_manager() -> tuple[InteractionManager, DummyBroadcaster]:
    bc = DummyBroadcaster()
    return InteractionManager.__new__(InteractionManager), bc


def test_vote_one_person_one_vote():
    v = Vote("主角先去哪？", ["石门", "撤离"], 60)
    assert v.cast("甲", 0) and v.cast("乙", 1)
    assert v.cast("甲", 1) is True  # 重复投覆盖：改投选项2，总票数不变
    assert v.tally() == [0, 2]
    assert v.cast("丙", 5) is False  # 越界选项拒绝
    v.open = False
    assert v.cast("丁", 0) is False  # 关闭后不再收票


def test_vote_result_text():
    v = Vote("主角先去哪？", ["石门", "撤离"], 60)
    v.cast("甲", 0)
    v.cast("乙", 0)
    text = v.result_text()
    assert "石门 2票" in text and "撤离 0票" in text and "石门」" in text


def test_material_pool_dedup_and_capacity():
    pool = MaterialPool(size=2)
    pool.add("素材A")
    pool.add("素材A")  # 去重
    pool.add("素材B")
    pool.add("素材C")  # 超容量淘汰最旧
    assert pool.digest() == "- 素材B\n- 素材C"
    pool.add("")      # 空文本忽略
    pool.add("  ")    # 纯空白忽略
    assert pool.digest() == "- 素材B\n- 素材C"


@pytest.mark.asyncio
async def test_ballot_intercept_and_close():
    """弹幕 1/2/3 计票命中返回 True；关闭后结果入素材池。"""
    manager, bc = make_manager()
    manager.config = type("C", (), {"interaction": type(
        "I", (), {"vote_enabled": True, "vote_duration": 10})()})()
    manager.broadcaster = bc
    manager.pool = MaterialPool()
    manager.vote = None
    manager._vote_task = None

    assert manager.handle_vote_ballot("甲", "1") is False  # 无投票不拦截
    await manager.open_vote("去哪？", ["石门", "撤离"], duration=999)
    assert manager.handle_vote_ballot("甲", "1") is True
    assert manager.handle_vote_ballot("乙", "B") is True
    assert manager.handle_vote_ballot("丙", "无关闲聊") is False  # 非选项不拦截（进闲聊）
    assert manager.vote.tally() == [1, 1]

    result = await manager.close_vote()
    assert result["ok"] and "观众更倾向" in result["result"]
    assert "观众投票「去哪？」" in manager.pool.digest()
    assert manager.handle_vote_ballot("丁", "1") is False  # 已关闭
    close_again = await manager.close_vote()
    assert not close_again["ok"]


@pytest.mark.asyncio
async def test_vote_countdown_no_crossfire():
    """倒计时只关「发起时那场」投票：当前投票已换成新一场时，旧倒计时不得误伤。"""
    manager, bc = make_manager()
    manager.config = type("C", (), {"interaction": type(
        "I", (), {"vote_enabled": True, "vote_duration": 10})()})()
    manager.broadcaster = bc
    manager.pool = MaterialPool()
    manager._vote_task = None
    vote_a = Vote("问题A", ["甲", "乙"], 0)   # duration=0：sleep 瞬时返回
    vote_b = Vote("问题B", ["甲", "乙"], 0)
    manager.vote = vote_b  # 当前进行中的是 B（A 已被手动关闭并换场）

    await manager._countdown(vote_a)  # 旧倒计时到点：不关 B
    assert vote_b.open, "旧投票的倒计时误关了新投票"
    await manager._countdown(vote_b)  # 自己那场到点：正常关闭
    assert not vote_b.open


@pytest.mark.asyncio
async def test_gift_event_fallback_and_pool():
    """礼物：LLM 未生成时走档位兜底文案，氛围素材必入池。"""
    manager, bc = make_manager()
    manager.config = type("C", (), {"interaction": type(
        "I", (), {"gift_events_enabled": True, "gift_rules": {"火箭": "神秘贵人登场"}})()})()
    manager.broadcaster = bc
    manager.pool = MaterialPool()
    manager._gift_last = {}

    async def no_llm(self, user_prompt):  # 注入：跳过真实 LLM
        raise AssertionError("映射表命中不应调用 LLM")

    manager._chat = no_llm.__get__(manager)
    await manager.handle_gift({"nick": "大佬袁", "gift_item": "火箭", "gift_num": 1})
    digest = manager.pool.digest()
    assert "大佬袁 送出 火箭×1" in digest and "[礼物剧情事件] 神秘贵人登场" in digest
    kinds = [m["type"] for m in bc.msgs]
    assert kinds == ["gift_event"]

    # 未映射礼物：无 novel（LLM 跳过）→ 兜底文案
    manager.get_novel = lambda: None
    manager._gift_last.clear()
    manager._gift_last["玫瑰"] = 0.0 - GIFT_COOLDOWN  # 冷却已过（用过去时间戳）
    import time
    manager._gift_last["玫瑰"] = time.monotonic() - GIFT_COOLDOWN - 1
    await manager.handle_gift({"nick": "书友", "gift_item": "玫瑰", "gift_num": 3})
    assert "[礼物剧情事件]" in manager.pool.digest() and "玫瑰×3" in manager.pool.digest()
