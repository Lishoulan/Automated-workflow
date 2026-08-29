from toutiao_content_factory.services.article_sanitizer import clean_article_text


def test_clean_article_text_never_pads_with_unreferenced_facts() -> None:
    article = "据公开材料显示，储能业务同比增长67%。"

    cleaned = clean_article_text(article)

    assert cleaned == article
    assert "12到18个月" not in cleaned
    assert "核心零部件占整机成本超过六成" not in cleaned


def test_clean_article_text_removes_inline_internal_labels_and_bad_punctuation() -> None:
    article = (
        "目前能确认的数据有限，manual_paste：产业链上游材料；"
        "manual_paste：后续应继续跟踪；来源摘要：manual_paste。"
        "口径以公开材料为准。"
    )

    cleaned = clean_article_text(article, min_word_count=0)

    assert "manual_paste" not in cleaned.lower()
    assert "来源摘要" not in cleaned
    assert "；；" not in cleaned
    assert "公开材料为准" in cleaned


def test_clean_article_text_is_idempotent_and_deduplicates_blocks() -> None:
    paragraph = "公开材料显示，客户验收仍是判断订单能否转化为收入的关键环节。"
    article = f"{paragraph}\n\n{paragraph}"

    once = clean_article_text(article, min_word_count=0)
    twice = clean_article_text(once, min_word_count=0)

    assert once == twice
    assert once.count("客户验收仍是判断订单能否转化为收入的关键环节") == 1
