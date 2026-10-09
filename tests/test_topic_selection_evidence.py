from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.topic_selection_evidence import selection_evidence


def topic(source="ithome", url="https://example.com/report"):
    return TopicCandidate(title="实际事件", url=url, source=source, source_type="rss")


def test_media_name_or_long_summary_does_not_prove_original_material():
    item = topic()
    item.snippet = "行业新闻报道" * 500
    item.evidence_strength = 100
    report = selection_evidence(item)
    assert report["status"] == "report_url_needs_retrieval" and report["assessment"]["passed"] is False
    assert selection_evidence(topic("toutiao_hot"))["priority"] == 2


def test_bound_body_is_prioritized_but_unrelated_background_is_not():
    item = topic()
    body = "。".join(f"公司公告第{i}项产品已经完成验证，实际部署超过{i+1}台设备，明确说明应用条件" for i in range(40))
    item.source_records = [{"source_id": "src_1", "title": item.title, "url": item.url, "original_text": body}]
    report = selection_evidence(item)
    assert report["priority"] == 0 and report["assessment"]["passed"] is True
    item.url = "https://example.com/different"
    assert selection_evidence(item)["priority"] == 1


def test_selection_diagnostics_strip_secrets_and_do_not_embed_originals():
    item = topic(url="https://example.com/report?token=private")
    report = selection_evidence(item)
    assert report["source_url"] == "https://example.com/report"
    assert "original_text" not in str(report) and "private" not in str(report)


def test_retrieved_hot_topic_keeps_real_source_urls_and_stable_priority():
    from toutiao_content_factory.services.automation import TopicAutomationService
    ready = topic("toutiao_hot")
    ready._verified_source_material = True
    ready._material_evidence_assessment = {"passed": True, "total_chars": 1200}
    ready._retrieved_source_urls = ["https://court.gov.cn/report?token=private", "https://court.gov.cn/report"]
    report = selection_evidence(ready)
    assert report["priority"] == 0 and report["source_urls"] == ["https://court.gov.cn/report"]
    first, second = topic(), topic()
    headline = topic("baidu_hot")
    service = object.__new__(TopicAutomationService)
    assert service._prioritize_evidence_rich_topics([headline, first, ready, second]) == [ready, first, second, headline]
