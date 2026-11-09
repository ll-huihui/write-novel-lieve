"""清洗管道单测（T3）。"""

from src.cleaner import (
    check_paragraph,
    clean_paragraph,
    extract_hook,
    is_pure_narrative,
    strip_hook_echo,
)


def test_clean_strips_markdown_and_meta():
    raw = "好的，以下是续写内容：\n### 第一章\n夜色**沉**入群山。风声呜咽。\n\n---\n（作者按语：待续）"
    out = clean_paragraph(raw)
    assert "###" not in out and "**" not in out and "---" not in out
    assert "按语" not in out and "以下是" not in out
    assert "夜色" in out


def test_clean_strips_think():
    out = clean_paragraph("<think>先想想剧情</think>他推开了那扇门。")
    assert "先想想" not in out and "他推开了那扇门。" in out


def test_pure_narrative_rules():
    ok, _ = is_pure_narrative("夜色沉入群山，只有山道上的一点灯火还在摇曳，像是给归人留的一句叮咛。")
    assert ok
    ok, reason = is_pure_narrative("太短")
    assert not ok and "过短" in reason
    ok, reason = is_pure_narrative("hello world this is english text only no chinese here at all")
    assert not ok and "中文占比" in reason
    ok, reason = is_pure_narrative("前文提到过这把刀的来历，此处不再赘述，夜色渐深。夜风掠过屋脊，铜铃轻响。")
    assert not ok and "元话语" in reason


def test_check_paragraph_hook_dup():
    prev_hook = "他推开门，里面竟站着十年前死去的人。"
    ok, issues = check_paragraph("他推开门，里面竟站着十年前死去的人。他的手抖得几乎握不住门把手。", prev_hook)
    assert not ok and any("重复" in i for i in issues)
    ok, issues = check_paragraph("门后的身影缓缓转过身来，苍白的脸上没有一丝血色，屋里静得能听见彼此的呼吸声。", prev_hook)
    assert ok and not issues


def test_check_paragraph_sentence_end():
    # 收句：句号/问号/叹号/省略号收尾（可带引号收束）都通过
    for tail in ("。", "！", "？", "……", "。”", "！”"):
        text = f"夜色沉入群山，只有山道上的一点灯火还在摇曳，像是给归人留的一句叮咛{tail}"
        ok, issues = check_paragraph(text)
        assert ok and not issues, tail
    # 未收句：段尾断在半句话 → 拒绝（章末断句防护）
    ok, issues = check_paragraph("周遭的空间维度如同被")
    assert not ok and any("收句" in i for i in issues)
    ok, issues = check_paragraph("夜色沉入群山，只有山道上的一点灯火还在摇曳，像是给归人留的")
    assert not ok and any("收句" in i for i in issues)


def test_extract_hook():
    # 逗号是弱停顿不分句，最后一句到强标点为止
    assert extract_hook("夜风很冷。他回头一看，身后站着一个人！") == "他回头一看，身后站着一个人！"
    # 句末右引号并回前一句，不能单独成"钩子"
    assert extract_hook("尘埃在耳膜上炸开——“你们不该看到这一切。”") == "尘埃在耳膜上炸开——“你们不该看到这一切。”"
    assert extract_hook("只有一句没有标点的话") == "只有一句没有标点的话"
    assert extract_hook("") == ""


def test_strip_hook_echo():
    hook = "他推开门，里面竟站着十年前死去的人。"
    # 完整复读钩子后接正文：剥掉钩子保留续写
    out = strip_hook_echo(hook + "他的手抖得几乎握不住门把手。", hook)
    assert out == "他的手抖得几乎握不住门把手。"
    # 前缀复读（模型改了句尾标点）：按首句剥（首句以强标点收尾）
    out = strip_hook_echo("他推开门，里面竟站着十年前死去的人！他的手抖得几乎握不住门把手。", hook)
    assert out == "他的手抖得几乎握不住门把手。"
    # 前缀复读但整段无句界（强标点在结尾）：剥完为空 → 保留原文
    out = strip_hook_echo("他推开门，里面竟站着十年前死去的人，他的手抖得几乎握不住门把手。", hook)
    assert out.startswith("他推开门")
    # 没复读：原样返回
    assert strip_hook_echo("门后的身影缓缓转过身来，苍白的脸上没有一丝血色。", hook).startswith("门后")
    # 剥完为空：保留原文交规则校验处理
    assert strip_hook_echo(hook, hook) == hook
    # 无钩子：原样返回
    assert strip_hook_echo("任意正文。", "") == "任意正文。"
