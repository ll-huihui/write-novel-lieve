"""小说持久化单测（T3）。"""

from src.novel_store import ChapterModel, NovelModel, NovelStore


def make_novel() -> NovelModel:
    return NovelModel(id="test01", title="测试书", genre="玄幻", target_words=100, target_chapters=2)


def test_create_get_save(tmp_path):
    store = NovelStore(root=tmp_path)
    novel = store.create(make_novel())
    assert novel.id == "test01"
    loaded = store.get("test01")
    assert loaded.title == "测试书"
    assert "test01" in store.list_ids()


def test_get_missing_raises(tmp_path):
    store = NovelStore(root=tmp_path)
    try:
        store.get("nope")
        assert False, "应当抛出 ValueError"
    except ValueError:
        pass


def test_id_rejects_path_traversal(tmp_path):
    """非法 ID（路径遍历/空）一律 ValueError，不得读出数据目录之外的文件。"""
    store = NovelStore(root=tmp_path)
    for bad in ("..", "../x", "a/b", "a\\b", "..%2f..%2fconfig", "", ".hidden"):
        try:
            store.get(bad)
            assert False, f"非法 ID 未被拒绝：{bad!r}"
        except ValueError:
            pass


def test_paragraph_flow_and_word_count(tmp_path):
    store = NovelStore(root=tmp_path)
    novel = store.create(make_novel())
    store.add_paragraph(novel, "第一段" * 10, "钩子一。")
    store.add_paragraph(novel, "第二段" * 10, "钩子二。")
    novel = store.get("test01")
    assert len(novel.chapters) == 1 and len(novel.chapters[0].paragraphs) == 2
    words = store.chapter_words(novel.chapters[0])
    assert words == 60  # 每段 30 字 × 2
    assert store.total_words(novel) == 60


def test_replace_paragraph_keeps_review_trace(tmp_path):
    store = NovelStore(root=tmp_path)
    novel = store.create(make_novel())
    store.add_paragraph(novel, "原段落" * 10, "钩子。")
    # 审核重写：rewritten=True；人工编辑：edited=True，两者标记独立保留
    store.replace_paragraph(novel, 1, 1, "重写后的段落" * 10, "新钩子。", rewritten=True)
    store.replace_paragraph(novel, 1, 1, "编辑后的段落" * 10, "新钩子。", edited=True)
    novel = store.get("test01")
    para = novel.chapters[0].paragraphs[0]
    assert para.text.startswith("编辑后") and para.edited is True and para.rewritten is True


def test_current_chapter_autocreate(tmp_path):
    store = NovelStore(root=tmp_path)
    novel = store.create(make_novel())
    ch = store.current_chapter(novel)
    assert ch.index == 1
