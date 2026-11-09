"""小说持久化：每本书一个 JSON 文件（data/novels/<id>.json）。"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from .config import DATA_DIR

# id 由 uuid4().hex[:10] 生成；收窄字符集可拒绝 ..%2f 等路径遍历（URL 解码发生在路由后）
_ID_RE = re.compile(r"[a-z0-9_-]{1,64}")


class ParagraphModel(BaseModel):
    text: str
    hook: str = ""
    edited: bool = False
    issues: list[str] = Field(default_factory=list)  # 段级规则校验问题（轻微）
    rewritten: bool = False  # 审核后是否被重写过


class ChapterModel(BaseModel):
    index: int  # 1 起
    title: str = ""
    summary: str = ""  # 章末摘要（章末流转时生成）
    review: dict | None = None  # 章级审核报告
    paragraphs: list[ParagraphModel] = Field(default_factory=list)


class NovelModel(BaseModel):
    id: str
    title: str = ""
    genre: str = ""
    worldview: str = ""  # 世界观设定
    characters: list[dict] = Field(default_factory=list)  # [{name, persona, voice?}] voice=音色库目录名（T7 建结构，角色配音后续版本启用）
    outline: str = ""  # 分卷大纲（可选）
    roadmap: str = ""  # 剧情路线图（无大纲时 AI 维护）
    status: str = "writing"  # writing | finished | archived
    target_words: int = 800000
    target_chapters: int = 150
    chapters: list[ChapterModel] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)


class NovelStore:
    def __init__(self, root: Path | None = None):
        self.root = root or (DATA_DIR / "novels")
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- 基础 CRUD ----

    def create(self, novel: NovelModel) -> NovelModel:
        if not novel.id:
            novel.id = uuid.uuid4().hex[:10]
        self.save(novel)
        return novel

    def _path(self, novel_id: str) -> Path:
        if not _ID_RE.fullmatch(novel_id or ""):
            raise ValueError(f"非法的小说 ID：{novel_id!r}")
        return self.root / f"{novel_id}.json"

    def get(self, novel_id: str) -> NovelModel:
        path = self._path(novel_id)
        if not path.exists():
            raise ValueError(f"小说不存在：{novel_id}")
        return NovelModel.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save(self, novel: NovelModel) -> None:
        self._path(novel.id).write_text(novel.model_dump_json(indent=2), encoding="utf-8")

    def list_ids(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.json"))

    # ---- 结构操作 ----

    def current_chapter(self, novel: NovelModel) -> ChapterModel:
        if not novel.chapters:
            # 首章：AI 提议的章名要在章末流转后才生成，先给默认标题
            novel.chapters.append(ChapterModel(index=1, title="第一章"))
        return novel.chapters[-1]

    def add_paragraph(self, novel: NovelModel, text: str, hook: str, issues: list[str] | None = None,
                      rewritten: bool = False) -> ParagraphModel:
        para = ParagraphModel(text=text, hook=hook, issues=issues or [], rewritten=rewritten)
        self.current_chapter(novel).paragraphs.append(para)
        self.save(novel)
        return para

    def replace_paragraph(self, novel: NovelModel, chapter_index: int, para_index: int,
                          text: str, hook: str, edited: bool = False,
                          rewritten: bool = False) -> None:
        """替换段落（审核重写或人工编辑），保留原有痕迹标记。"""
        chapter = next(c for c in novel.chapters if c.index == chapter_index)
        old = chapter.paragraphs[para_index - 1]
        chapter.paragraphs[para_index - 1] = ParagraphModel(
            text=text, hook=hook, edited=old.edited or edited, issues=old.issues,
            rewritten=old.rewritten or rewritten
        )
        self.save(novel)

    # ---- 统计 ----

    @staticmethod
    def chapter_words(chapter: ChapterModel) -> int:
        return sum(len(p.text.replace(" ", "").replace("\n", "")) for p in chapter.paragraphs)

    @staticmethod
    def total_words(novel: NovelModel) -> int:
        return sum(NovelStore.chapter_words(c) for c in novel.chapters)
