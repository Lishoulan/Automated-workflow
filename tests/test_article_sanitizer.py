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


def test_clean_article_text_removes_leaked_source_labels() -> None:
    article = "材料目前能支持的事实是：机器人前瞻：优必选公布半年报；机器人前瞻：全尺寸人形机器人收入增长。"

    cleaned = clean_article_text(article, min_word_count=0)

    assert "机器人前瞻" not in cleaned
    assert "优必选公布半年报" in cleaned
    assert "全尺寸人形机器人收入增长" in cleaned


def test_clean_article_text_removes_copied_byline_account_cta_and_hashtags() -> None:
    article = (
        "机器人在热，兑现偏冷，优必选半年卖了16123台人形机器人。\n\n"
        "机器人前瞻（公众号：robot_pro） 作者 | 钟宸编辑 | 漠影看着提速，实则分化。"
        "后续判断仍要回到订单和回款。\n\n"
        "关注我。聚焦科技商业、AI产业与先进制造，用数据拆热点。\n\n"
        "#机器人 #具身智能 #AI #科技"
    )

    cleaned = clean_article_text(article, min_word_count=0)

    assert "优必选" in cleaned
    assert "16123台" in cleaned
    assert "看着提速，实则分化" in cleaned
    assert "作者" not in cleaned
    assert "编辑" not in cleaned
    assert "robot_pro" not in cleaned
    assert "关注我" not in cleaned
    assert "#机器人" not in cleaned
    assert "#具身智能" not in cleaned


def test_clean_article_text_preserves_body_after_compact_byline() -> None:
    article = "机器人前瞻（公众号：robot_pro） 作者 | 钟宸 编辑 | 漠影看着提速，实则分化"

    cleaned = clean_article_text(article, min_word_count=0)

    assert cleaned == "看着提速，实则分化"


def test_clean_article_text_removes_internal_review_paragraphs() -> None:
    article = (
        "公开材料显示，项目已经进入验证阶段。\n\n"
        "主体关系也不能省略，当前记录涉及某公司。主体出现在材料中，只能说明它与事件有关。\n\n"
        "后续需要看客户验收和交付节奏。"
    )

    cleaned = clean_article_text(article, min_word_count=0)

    assert "主体关系也不能省略" not in cleaned
    assert "公开材料显示" in cleaned
    assert "后续需要看客户验收" in cleaned


def test_clean_article_text_joins_non_terminal_fragmented_blocks() -> None:
    article = "接下来判断可以看订单转化率、实际交付量、制造、\n\n物流和商业服务场景的客户复购率。"

    cleaned = clean_article_text(article, min_word_count=0)

    assert "制造、物流和商业服务" in cleaned
    assert "制造、\n\n物流" not in cleaned


def test_clean_article_text_keeps_lead_that_looks_like_a_title() -> None:
    article = (
        "这份中期业绩释放的信号很明确：收入结构发生变化，但利润模型尚未定型。"
        "据官方公告，公司收入同比增长104.2%。"
    )

    cleaned = clean_article_text(
        article,
        title_candidates=["示例标题"],
        min_word_count=0,
    )

    assert cleaned.startswith("这份中期业绩释放的信号很明确：")
    assert "收入结构发生变化" in cleaned
