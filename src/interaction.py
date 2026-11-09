"""互动素材通路（T8）：投票状态机、礼物事件路由、观众素材池。

原则（产品文档 §4.4 素材/礼物通路）：投票与礼物只产出「观众素材」文本，
供续写 prompt 参考，绝不直接改写正文、绝不进闲聊历史。

礼物事件三级命中：配置映射表 → LLM 现场生成（自动回写映射表）→ 档位兜底文案。
"""
from __future__ import annotations

import asyncio
import json
import time

from .broadcaster import StageBroadcaster
from .config import Config, save_config
from .llm import LLMError, stream_chat, strip_think
from .writer.prompts import gift_event_user, vote_question_user

POOL_SIZE = 10        # 素材池容量（超出淘汰最旧）
GIFT_COOLDOWN = 60    # 同名礼物 LLM 事件生成冷却（秒）
VOTE_CHOICES = {"1": 0, "2": 1, "3": 2, "a": 0, "b": 1, "c": 2}
# 档位兜底文案（LLM 不可用时按礼物价值 rough 分档）
TIER_FALLBACK = "直播间气氛被这份心意点燃，故事里也仿佛多了一分暖意与转机。"


class MaterialPool:
    """观众素材池：投票结果 + 礼物事件，FIFO 容量上限，去重。"""

    def __init__(self, size: int = POOL_SIZE):
        self._items: list[str] = []
        self._size = size

    def add(self, text: str) -> None:
        t = (text or "").strip()
        if t and t not in self._items:
            self._items.append(t)
            if len(self._items) > self._size:
                self._items.pop(0)

    def digest(self) -> str:
        return "\n".join(f"- {t}" for t in self._items)


class Vote:
    """单次投票：一人一票（重复投覆盖），开/关两态。"""

    def __init__(self, question: str, options: list[str], duration: int):
        self.question = question
        self.options = options
        self.duration = duration
        self.open = True
        self.ballots: dict[str, int] = {}  # nick -> 选项下标

    def cast(self, nick: str, choice: int) -> bool:
        if not self.open or not (0 <= choice < len(self.options)):
            return False
        self.ballots[nick] = choice
        return True

    def tally(self) -> list[int]:
        counts = [0] * len(self.options)
        for c in self.ballots.values():
            counts[c] += 1
        return counts

    def result_text(self) -> str:
        counts = self.tally()
        parts = [f"{opt} {n}票" for opt, n in zip(self.options, counts)]
        leader = counts.index(max(counts)) if any(counts) else -1
        lean = f"，观众更倾向「{self.options[leader]}」" if leader >= 0 else "（无人投票）"
        return f"观众投票「{self.question}」：{' / '.join(parts)}{lean}"


class InteractionManager:
    """聚合素材池 + 当前投票 + 礼物路由；由 main 创建并注入 loop / api / 弹幕入口。"""

    def __init__(self, config: Config, broadcaster: StageBroadcaster,
                 get_novel: "callable | None" = None):
        self.config = config
        self.broadcaster = broadcaster
        self.get_novel = get_novel  # () -> NovelModel | None
        self.pool = MaterialPool()
        self.vote: Vote | None = None
        self._gift_last: dict[str, float] = {}   # 礼物名 -> 上次 LLM 生成时间
        self._vote_task: asyncio.Task | None = None

    # ---- 弹幕入口（main 把 danmaku.on_danmaku 指到这里，再分流） ----

    async def handle_danmaku(self, msg: dict, chat_host) -> None:
        if msg.get("kind") == "gift":
            await self.handle_gift(msg)
            return
        if msg.get("kind") == "chat" and self.handle_vote_ballot(
                msg.get("nick", ""), msg.get("content", "")):
            return  # 命中投票计票，不再进闲聊
        await chat_host.handle_danmaku(msg)

    # ---- 投票 ----

    async def open_vote(self, question: str, options: list[str],
                        duration: int | None = None) -> dict:
        if not self.config.interaction.vote_enabled:
            return {"ok": False, "error": "投票未开启（互动配置）"}
        question, options = question.strip(), [o.strip() for o in options if o.strip()]
        if not question or len(options) < 2:
            return {"ok": False, "error": "需要问题与至少 2 个选项"}
        if self.vote and self.vote.open:
            return {"ok": False, "error": "已有进行中的投票"}
        self.vote = Vote(question, options[:3], max(10, duration or self.config.interaction.vote_duration))
        await self.broadcaster.broadcast({
            "type": "vote_open", "question": question,
            "options": self.vote.options, "duration": self.vote.duration,
            **self.vote_payload()})
        self._cancel_vote_task()  # 上一场投票的倒计时若还在睡，先取消
        self._vote_task = asyncio.create_task(self._countdown(self.vote))
        return {"ok": True}

    def _cancel_vote_task(self) -> None:
        if self._vote_task and not self._vote_task.done():
            self._vote_task.cancel()
        self._vote_task = None

    async def _countdown(self, vote: Vote) -> None:
        """倒计时到点关票。绑定「发起时那场」投票：期间手动关闭或换新投票时不误伤。"""
        await asyncio.sleep(vote.duration)
        if self.vote is vote and vote.open:
            await self.close_vote()

    async def close_vote(self) -> dict:
        vote = self.vote
        if not vote or not vote.open:
            return {"ok": False, "error": "没有进行中的投票"}
        vote.open = False
        self._cancel_vote_task()  # 手动关票后倒计时不再等点
        result = vote.result_text()
        self.pool.add(result)
        await self.broadcaster.broadcast({
            "type": "vote_close", "question": vote.question,
            "options": vote.options, "tally": vote.tally(), "result": result})
        return {"ok": True, "result": result}

    def handle_vote_ballot(self, nick: str, content: str) -> bool:
        """投票开启时弹幕 1/2/3（或 A/B/C）计票；命中返回 True。"""
        vote = self.vote
        if not vote or not vote.open:
            return False
        idx = VOTE_CHOICES.get(content.strip().lower())
        if idx is None or not vote.cast(nick, idx):
            return False
        asyncio.create_task(self.broadcaster.broadcast({
            "type": "vote_update", "tally": vote.tally(), "total": len(vote.ballots)}))
        return True

    async def generate_question(self) -> dict:
        """AI 据当前剧情出题（只出候选，不发起；控制台可改后手动发起）。"""
        novel = self.get_novel() if self.get_novel else None
        if novel is None:
            return {"ok": False, "error": "当前没有直播中的书"}
        summaries = "\n".join(
            f"第{c.index}章《{c.title}》：{c.summary}" for c in novel.chapters if c.summary)
        direction = novel.outline or novel.roadmap or ""
        next_title = novel.chapters[-1].title if novel.chapters else ""
        raw = await self._chat(vote_question_user(direction, summaries, next_title))
        try:
            data = json.loads(strip_think(raw))
            return {"ok": True, "question": str(data["question"]),
                    "options": [str(o) for o in data["options"]][:3]}
        except (json.JSONDecodeError, KeyError, TypeError):
            return {"ok": False, "error": "出题解析失败"}

    async def auto_generate_and_open(self) -> dict:
        """章末自动出题并直接发起投票。"""
        gen = await self.generate_question()
        if not gen.get("ok"):
            return gen
        return await self.open_vote(gen["question"], gen["options"])

    def vote_payload(self) -> dict:
        """快照/重连用：当前投票状态。"""
        vote = self.vote
        if not vote:
            return {}
        return {"question": vote.question, "options": vote.options,
                "tally": vote.tally(), "open": vote.open,
                "result": "" if vote.open else vote.result_text()}

    # ---- 礼物事件（三级命中） ----

    async def handle_gift(self, msg: dict) -> None:
        if not self.config.interaction.gift_events_enabled:
            return
        nick = msg.get("nick", "观众")
        item, num = msg.get("gift_item", "礼物"), int(msg.get("gift_num") or 1)
        self.pool.add(f"观众 {nick} 送出 {item}×{num}，直播间气氛热烈")  # 氛围素材必入池
        event = self.config.interaction.gift_rules.get(item)
        if not event and self._cooldown_ok(item):
            self._gift_last[item] = time.monotonic()
            event = await self._generate_gift_event(item, num)  # LLM 现场生成
            if event:
                self.config.interaction.gift_rules[item] = event  # 回写映射表（下次直接命中）
                save_config(self.config)
        self.pool.add(f"[礼物剧情事件] {event or TIER_FALLBACK}")
        await self.broadcaster.broadcast({
            "type": "gift_event", "nick": nick, "gift": item, "num": num,
            "event": event or TIER_FALLBACK})

    def _cooldown_ok(self, item: str) -> bool:
        last = self._gift_last.get(item)
        return last is None or time.monotonic() - last >= GIFT_COOLDOWN

    async def _generate_gift_event(self, item: str, num: int) -> str:
        novel = self.get_novel() if self.get_novel else None
        if novel is None:
            return ""
        try:
            raw = await self._chat(
                gift_event_user(novel.title, novel.genre, item, num))
        except LLMError:
            return ""
        return strip_think(raw).replace("\n", "").strip()[:60]

    # ---- LLM ----

    async def _chat(self, user_prompt: str) -> str:
        profile = self.config.llm.chat if self.config.llm.chat.preset else self.config.llm.writing
        parts = [p async for p in stream_chat(
            profile, [{"role": "user", "content": user_prompt}],
            custom_presets={k: v.model_dump() for k, v in self.config.llm.presets.items()})]
        return "".join(parts)
