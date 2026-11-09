"""写作引擎：状态机 + 生命周期 + 段级规则校验 + 章级 LLM 审核处置。

审核策略（用户拍板）：每段规则校验（零成本零延迟）+ 每章一次 LLM 深度审核；
严重问题定位重写一次，仍不过转"已暂停"待人工处理。
"""
from __future__ import annotations

import re

from ..cleaner import check_paragraph, clean_paragraph, extract_hook, strip_hook_echo
from ..config import Config
from ..llm import LLMError, stream_chat, strip_think
from ..novel_store import ChapterModel, NovelModel, NovelStore
from . import prompts as P
from .review import extract_json_block, parse_novel_setting

# ---- 状态 ----
STATE_IDLE = "idle"
STATE_WRITING = "writing"
STATE_GAP = "gap"          # T4/T6 互动间隙启用
STATE_PAUSED = "paused"
STATE_ENDING = "ending"    # 收官模式
STATE_FINISHED = "finished"


class WritingEngine:
    def __init__(self, config: Config, store: NovelStore | None = None):
        self.config = config
        self.store = store or NovelStore()
        self.state = STATE_IDLE
        self.current_novel_id: str | None = None
        self._state_before_pause = STATE_IDLE

    # ---- 配置与档位 ----

    def _profile(self, role: str):
        """取角色档位；review 等未配置（preset 为空）时回退写作模型。"""
        llm = self.config.llm
        profile = getattr(llm, role)
        if role != "writing" and not profile.preset:
            return llm.writing
        return profile

    def _presets(self) -> dict:
        return {k: v.model_dump() for k, v in self.config.llm.presets.items()}

    async def _chat(self, role: str, system: str, user: str) -> str:
        parts: list[str] = []
        async for delta in stream_chat(
            self._profile(role),
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            custom_presets=self._presets(),
        ):
            parts.append(delta)
        return strip_think("".join(parts))

    def _novel(self, novel_id: str | None = None) -> NovelModel:
        nid = novel_id or self.current_novel_id
        if not nid:
            raise ValueError("没有当前小说，请先生成新书")
        return self.store.get(nid)

    def _w(self):
        return self.config.writing

    def _recent_paragraphs(self, novel: NovelModel) -> list[str]:
        """最近 N 段原文（时间正序），上下文组装用。"""
        recent: list[str] = []
        for c in reversed(novel.chapters):
            for p in reversed(c.paragraphs):
                recent.append(p.text)
                if len(recent) >= self._w().context_recent_paras:
                    break
            if len(recent) >= self._w().context_recent_paras:
                break
        return list(reversed(recent))

    # ---- 生命周期：生成新书 ----

    async def generate_novel(self, genre_hint: str = "", idea: str = "",
                             target_words: int = 0, target_chapters: int = 0) -> NovelModel:
        """生成新书设定卡入库。设定卡可给题材方向/创意提示；目标不传用全局配置。"""
        w = self._w()
        tw = target_words or w.target_words
        tc = target_chapters or w.target_chapters
        user = P.novel_gen_user(tw, tc, genre_hint, idea)
        data = parse_novel_setting(await self._chat("writing", "你是资深的中文小说策划编辑。", user))
        if not data:
            # 小模型输出不稳：重试一次
            data = parse_novel_setting(await self._chat(
                "writing", "你是资深的中文小说策划编辑。",
                user + "\n（上一次输出无法解析，请严格按【书名】等标记分段输出）"))
        if not data:
            raise LLMError("新书生成失败，模型输出无法解析（缺少【书名】等标记）")
        novel = NovelModel(
            id="",
            title=str(data["title"]),
            genre=str(data.get("genre", "")),
            worldview=str(data.get("worldview", "")),
            characters=list(data.get("characters") or []),
            outline=str(data.get("outline", "")),
            status="writing",
            target_words=tw,
            target_chapters=tc,
        )
        self.store.create(novel)
        self.current_novel_id = novel.id
        self.state = STATE_IDLE  # 有了书但还没开头
        return novel

    def archive(self, novel_id: str) -> dict:
        """归档书籍：只读；若正被直播使用则同时移出直播。"""
        novel = self._novel(novel_id)
        novel.status = "archived"
        self.store.save(novel)
        note = ""
        if self.current_novel_id == novel_id:
            self.current_novel_id = None
            self.state = STATE_IDLE
            note = "该书正在直播，已同时移出（写作循环需重新选书）"
        return {"archived": novel.id, "note": note}

    def bind_voices(self, novel_id: str, bindings: list[dict]) -> int:
        """角色-音色绑定：按角色名写回 characters[].voice（T7 建结构，配音后续版本启用）。"""
        novel = self._novel(novel_id)
        by_name = {str(b.get("name")): str(b.get("voice", "")) for b in bindings}
        n = 0
        for c in novel.characters:
            if c.get("name") in by_name:
                c["voice"] = by_name[c["name"]]
                n += 1
        self.store.save(novel)
        return n

    # ---- 生命周期：生成开头 ----

    async def generate_opening(self, novel_id: str) -> dict:
        novel = self._novel(novel_id)
        w = self._w()
        parts = await self._write_paragraph(novel, P.opening_task(w.para_min, w.para_max),
                                            prev_hook="")
        for t in parts:
            self.store.add_paragraph(novel, t, extract_hook(t))
        self.state = STATE_WRITING
        return {"paragraphs": parts, "hook": extract_hook(parts[-1]), "issues": [],
                "chapter_index": 1, "para_index": len(novel.chapters[0].paragraphs),
                "state": self.state}

    # ---- 段落生成（含段级规则校验） ----

    async def _write_paragraph(self, novel: NovelModel, task: str, prev_hook: str,
                               audience_material: str = "") -> list[str]:
        """生成一段并过段级规则校验，不合格带反馈重试一次。

        模型可能一次输出多个自然段（空行分隔）：整体过校验后按段拆分返回。
        """
        ending = self.state == STATE_ENDING
        system = P.SYSTEM_WRITER + (P.SYSTEM_ENDING_SUFFIX if ending else "")
        text, issues = "", []
        for _attempt in range(2):
            raw = await self._chat(
                "writing", system,
                P.context_user(novel, task, prev_hook, self._recent_paragraphs(novel),
                               audience_material=audience_material))
            text = strip_hook_echo(clean_paragraph(raw), prev_hook)
            ok, issues = check_paragraph(text, prev_hook, min_len=self._w().para_min)
            if ok:
                parts = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
                return parts or [text]
            task += f"\n（上一次输出不合格：{'；'.join(issues)}。请严格只输出正文并衔接上文。）"
        raise LLMError(f"段落两次生成均未通过规则校验：{'；'.join(issues)}；最后输出：{text[:120]}")

    # ---- 续写 ----

    async def continue_paragraph(self, novel_id: str | None = None,
                                 audience_material: str = "") -> dict:
        if self.state not in (STATE_WRITING, STATE_ENDING):
            raise ValueError(f"当前状态 {self.state} 不允许续写")
        novel = self._novel(novel_id)
        chapter = self.store.current_chapter(novel)
        prev_hook = chapter.paragraphs[-1].hook if chapter.paragraphs else ""
        w = self._w()
        parts = await self._write_paragraph(
            novel, P.continue_task(w.para_min, w.para_max, self.state == STATE_ENDING),
            prev_hook, audience_material=audience_material)
        last = None
        for t in parts:
            last = self.store.add_paragraph(novel, t, extract_hook(t))
        chapter_words = self.store.chapter_words(chapter)
        return {"paragraphs": parts, "hook": last.hook, "issues": last.issues,
                "chapter_index": chapter.index, "para_index": len(chapter.paragraphs),
                "chapter_words": chapter_words,
                "chapter_done": chapter_words >= w.chapter_max_words,  # 提示可触发章末流转
                "total_words": self.store.total_words(novel), "state": self.state}

    # ---- 章级审核 ----

    async def _review_chapter(self, novel: NovelModel, chapter: ChapterModel) -> dict:
        recent_n = self._w().review_recent_chapters
        summaries = "\n".join(
            f"第{c.index}章《{c.title}》：{c.summary}"
            for c in novel.chapters if c.summary and c.index < chapter.index
        )[-recent_n * 400:]
        raw = await self._chat("review", P.REVIEW_SYSTEM, P.review_user(chapter, summaries))
        data = extract_json_block(raw)
        if not data:
            # 审核输出解析失败：不阻塞写作，记录原始输出供人工查看
            return {"pass": True, "parse_failed": True, "raw": raw[:500]}
        data.setdefault("pass", True)
        data.setdefault("issues", [])
        return data

    async def _rewrite_paragraph(self, novel: NovelModel, chapter_index: int, para_index: int,
                                 issue_detail: str) -> bool:
        """按审核意见重写段落；重写后过规则校验才算成功。"""
        chapter = next(c for c in novel.chapters if c.index == chapter_index)
        old = chapter.paragraphs[para_index - 1]
        prev_hook = chapter.paragraphs[para_index - 2].hook if para_index >= 2 else ""
        w = self._w()
        parts = await self._write_paragraph(
            novel, P.rewrite_task(para_index, old.text, issue_detail, prev_hook,
                                  w.para_min, w.para_max), prev_hook)
        text = "\n".join(parts)  # 重写针对单段：模型多段输出时合并回一段
        ok, _ = check_paragraph(text, prev_hook, min_len=self._w().para_min)
        if not ok:
            return False
        self.store.replace_paragraph(novel, chapter_index, para_index, text,
                                     extract_hook(text), rewritten=True)
        return True

    # ---- 章末流转 ----

    async def chapter_end(self, novel_id: str | None = None) -> dict:
        novel = self._novel(novel_id)
        chapter = self.store.current_chapter(novel)
        if not chapter.paragraphs:
            raise ValueError("本章还没有正文，无法章末流转")

        review = await self._review_chapter(novel, chapter)
        severe = [i for i in review.get("issues", []) if i.get("severity") == "严重"]

        rewritten, unresolved = [], []
        if not review.get("pass") or severe:
            for issue in severe:
                idx = int(issue.get("para_index") or 0)
                if 1 <= idx <= len(chapter.paragraphs):
                    if await self._rewrite_paragraph(novel, chapter.index, idx, issue.get("detail", "")):
                        rewritten.append(idx)
                    else:
                        unresolved.append(issue)
                else:
                    unresolved.append(issue)

        if unresolved:
            # 严重问题重写后仍未解决：暂停写作，转人工处理
            self._state_before_pause = self.state
            self.state = STATE_PAUSED
            return {"paused": True, "review": review, "unresolved": unresolved,
                    "rewritten": rewritten, "state": self.state}

        # 摘要 + 下一章提议 + 路线图维护
        chapter.review = review  # 审核结果入库（人工可在控制台查看）
        chapter.summary = await self._summarize_chapter(novel, chapter)
        next_title = await self._propose_next_chapter(novel)
        novel.chapters.append(ChapterModel(index=chapter.index + 1, title=next_title))
        if self._w().roadmap_update_every > 0 and (chapter.index + 1) % self._w().roadmap_update_every == 0:
            novel.roadmap = await self._update_roadmap(novel)
        self.store.save(novel)
        self.state = STATE_WRITING
        return {"paused": False, "review": review, "rewritten": rewritten,
                "summary": chapter.summary, "next_chapter_title": next_title,
                "new_chapter_index": chapter.index + 1, "state": self.state}

    async def _summarize_chapter(self, novel: NovelModel, chapter: ChapterModel) -> str:
        text = "\n".join(p.text for p in chapter.paragraphs)
        raw = await self._chat("writing", "你是小说编辑，负责写剧情摘要。",
                               P.summarize_user(chapter, text))
        return clean_paragraph(raw).replace("\n", "")

    async def _propose_next_chapter(self, novel: NovelModel) -> str:
        summaries = "\n".join(f"第{c.index}章：{c.summary}" for c in novel.chapters if c.summary)
        direction = novel.outline or novel.roadmap or ""
        raw = await self._chat("writing", "你是小说策划编辑。", P.propose_user(direction, summaries))
        lines = clean_paragraph(raw).splitlines()
        return (lines[0].strip() if lines else f"第{len(novel.chapters) + 1}章")[:10]

    async def _update_roadmap(self, novel: NovelModel) -> str:
        summaries = "\n".join(f"第{c.index}章：{c.summary}" for c in novel.chapters if c.summary)
        remaining = max(1, novel.target_chapters - len(novel.chapters))
        return clean_paragraph(await self._chat(
            "writing", "你是小说策划编辑，负责长线剧情规划。",
            P.roadmap_user(novel.roadmap, summaries, remaining, novel.target_words)))

    # ---- 收官与完结 ----

    def _ending_check(self, novel: NovelModel) -> tuple[bool, str]:
        """篇幅保障：字数/章节数不达标禁止收官。"""
        words = self.store.total_words(novel)
        chapters = len(novel.chapters)
        if words < novel.target_words:
            return False, f"总字数未达标：{words}/{novel.target_words} 字，拒绝收官"
        if chapters < novel.target_chapters:
            return False, f"章节数未达标：{chapters}/{novel.target_chapters} 章，拒绝收官"
        return True, ""

    async def start_ending(self, novel_id: str) -> dict:
        novel = self._novel(novel_id)
        ok, reason = self._ending_check(novel)
        if not ok:
            raise ValueError(reason)
        self.state = STATE_ENDING
        return {"state": self.state, "total_words": self.store.total_words(novel)}

    async def finish(self, novel_id: str) -> dict:
        if self.state != STATE_ENDING:
            raise ValueError("只有收官模式下可以完结")
        novel = self._novel(novel_id)
        novel.status = "finished"
        self.store.save(novel)
        self.state = STATE_FINISHED
        return {"status": novel.status, "state": self.state, "total_words": self.store.total_words(novel)}

    # ---- 暂停/恢复（人工接管） ----

    def pause(self) -> dict:
        if self.state in (STATE_WRITING, STATE_GAP, STATE_ENDING):
            self._state_before_pause = self.state
            self.state = STATE_PAUSED
        return {"state": self.state}

    def resume(self) -> dict:
        if self.state == STATE_PAUSED:
            self.state = self._state_before_pause if self._state_before_pause != STATE_PAUSED else STATE_WRITING
        return {"state": self.state}
