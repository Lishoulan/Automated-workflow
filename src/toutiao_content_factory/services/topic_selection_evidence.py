"""Explain source availability without treating a publisher name as full prose."""
from __future__ import annotations

from dataclasses import asdict

from toutiao_content_factory.services.material_search_service import MaterialBundle, MaterialSource, assess_material_evidence
from toutiao_content_factory.services.wechat_evidence import public_url, scope_source_records


def selection_evidence(topic):
    url = public_url(str(getattr(topic, "url", "") or ""))
    records = [row for row in getattr(topic, "source_records", []) or [] if isinstance(row, dict)]
    records = scope_source_records(records, url, event_published_at=getattr(topic, "published_at", ""))
    bound = any(public_url(row.get("url", "")) == url for row in records) if url else False
    sources = [MaterialSource(title=str(row.get("title", "")), url=public_url(row.get("url", "")),
               body=str(row.get("original_text", "")), ok=True) for row in records
               if bound and row.get("source_role") in {"primary", "supporting"}
               and row.get("source_id") and row.get("original_text") and public_url(row.get("url", ""))]
    assessment = asdict(assess_material_evidence(MaterialBundle(topic.title, sources)))
    hydrated = getattr(topic, "_material_evidence_assessment", {}) or {}
    retrieved_urls = [cleaned for value in getattr(topic, "_retrieved_source_urls", []) or []
                      if (cleaned := public_url(str(value)))]
    if not sources and getattr(topic, "_verified_source_material", False) and hydrated.get("passed") is True:
        assessment = dict(hydrated)
    headline_only = str(getattr(topic, "source", "")).lower() in {"toutiao_hot", "baidu_hot"}
    if assessment.get("passed") is True:
        priority, status = 0, "source_material_ready"
    elif url and not headline_only:
        priority, status = 1, "report_url_needs_retrieval"
    else:
        priority, status = 2, "headline_needs_sources"
    return {"schema": 1, "priority": priority, "status": status, "source_url": url,
            "source_urls": list(dict.fromkeys([source.url for source in sources] + retrieved_urls)), "assessment": assessment,
            "reader_context": list(getattr(topic, "matched_keywords", []) or [])[:6],
            "note": "Selection priority is not factual approval. Retrieve original reports and pass the existing writing, originality and review checks before delivery."}
