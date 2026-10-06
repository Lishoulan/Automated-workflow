"""Minimal, sanitized evidence for a reviewed WeChat run."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def safe_text(value: Any) -> str:
    text = str(value)
    text = re.sub(r"https?://[^\s<>\"']+", "[URL omitted]", text)
    text = re.sub(r"(?i)(token|cookie|secret|password|api_key)\s*[:=]\s*\S+", r"\1=[redacted]", text)
    for name, secret in os.environ.items():
        if secret and len(secret) >= 8 and any(part in name for part in ("KEY", "TOKEN", "SECRET", "SESSION")):
            text = text.replace(secret, "[redacted]")
    return text[:1500]


def fresh_json(path: Path, started: int) -> dict[str, Any]:
    if not path.exists() or path.stat().st_mtime_ns < started:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Expected a run report object.")
    return payload


def review_diagnostics(settings: Any, native: dict[str, Any], started: int) -> dict[str, Any]:
    workflow = fresh_json(settings.automation_dir / "last_daily_workflow_report.json", started)
    attempts = workflow.get("attempts", [])
    trusted = (
        workflow.get("account_key") == settings.account_key
        and workflow.get("status") == "review_required"
        and isinstance(attempts, list) and bool(attempts)
        and all(isinstance(item, dict) for item in attempts)
    )
    attempted = not trusted or any(
        "platform_results" in item or "platform_errors" in item for item in attempts
    )
    phase = fresh_json(settings.automation_dir / "wechat_publish_phase.json", started)
    if (phase.get("account_key") == settings.account_key and phase.get("phase") == "preparing"
            and phase.get("publication_attempted") is False and not native.get("wechat_status")
            and isinstance(attempts, list) and not any("platform_results" in row or "platform_errors" in row
                                                     for row in attempts if isinstance(row, dict))):
        attempted = False
    if native.get("run_outcome") == "no_topics" and not native.get("wechat_status") and not native.get("error"):
        attempted = False
    result: dict[str, Any] = {
        "status": native.get("status", "error"), "publication_attempted": attempted,
        "attempt_count": len(attempts) if isinstance(attempts, list) else 0,
        "reason_codes": [], "attempts": [],
        "enrichment_count": workflow.get("enrichment_count", 0),
        "eligible_candidate_count": workflow.get("eligible_candidate_count", 0),
    }
    if not isinstance(attempts, list):
        return result
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        summary: dict[str, Any] = {"status": safe_text(attempt.get("status", "unknown"))}
        summary["reason"] = safe_text(attempt.get("skip_reason", ""))
        score = attempt.get("score_snapshot", {})
        if isinstance(score, dict):
            summary["scores"] = {key: value for key, value in score.items() if isinstance(value, (int, float, bool))}
            if score.get("length_shortfall", 0) or (
                isinstance(score.get("article_char_count"), (int, float))
                and isinstance(score.get("length_minimum"), (int, float))
                and score["article_char_count"] < score["length_minimum"]
            ):
                result["reason_codes"].append("article_too_short")
        run_dir = Path(str(attempt.get("run_dir", ""))).resolve()
        if run_dir.is_relative_to(settings.runs_dir.resolve()) and run_dir.is_dir():
            council = fresh_json(run_dir / "quality_review_council.json", started)
            if council:
                summary["quality_review"] = {
                    key: safe_text(council[key]) if isinstance(council[key], str) else council[key]
                    for key in ("verdict", "overall_score", "revision_round", "used_fallback")
                    if key in council and isinstance(council[key], (str, int, float, bool))
                }
                summary["required_revisions"] = [safe_text(item) for item in council.get("required_revisions", [])]
                for field in ("hard_blockers", "fact_issues", "logic_issues", "structure_issues", "originality_issues", "compliance_issues"):
                    summary[field] = [safe_text(item) for item in council.get(field, [])]
                if council.get("verdict") != "approved":
                    result["reason_codes"].append("quality_review_required")
            archived = fresh_json(run_dir / "workflow_result.json", started)
            content = archived.get("content", {})
            data = archived.get("data_report", {})
            if isinstance(data, dict):
                from toutiao_content_factory.services.wechat_evidence import public_url
                summary["article_kind"] = safe_text(data.get("article_kind", ""))
                summary["source_records"] = [{key: safe_text(source.get(key, ""))
                                               for key in ("source_id", "title", "publisher", "published_at")} | {"url": public_url(str(source.get("url", "")))}
                                              for source in data.get("source_records", []) if isinstance(source, dict)]
                summary["fact_evidence_count"] = len(data.get("fact_evidence", []))
                summary["draft_fingerprint"] = safe_text(data.get("draft_fingerprint", ""))
                summary["source_fingerprint"] = safe_text(data.get("source_fingerprint", ""))
            if isinstance(content, dict):
                summary["grounding_diagnostics"] = [{key: row[key] for key in
                    ("before_cleanup_chars", "after_cleanup_chars", "final_body_chars", "accepted_section_count", "returned_section_count")
                    if type(row.get(key)) is int} for row in content.get("grounding_diagnostics", []) if isinstance(row, dict)]
                summary["revision_trace"] = [{"revision_round": trace.get("revision_round", 0),
                    "issues": [{key: safe_text(issue.get(key, ""))[:350]
                                for key in ("issue_id", "category", "requirement", "before", "after", "status", "verification")}
                               | {"evidence_ids": [safe_text(value) for value in issue.get("evidence_ids", [])]}
                               for issue in trace.get("issues", []) if isinstance(issue, dict)]}
                    for trace in content.get("revision_trace", []) if isinstance(trace, dict)]
            validation = content.get("title_validation", {}) if isinstance(content, dict) else {}
            if isinstance(validation, dict) and validation:
                summary["title_validation"] = {
                    "status": safe_text(validation.get("status", "unknown")),
                    "reasons": {safe_text(key): value for key, value in validation.get("reason_summary", {}).items()
                                if isinstance(value, int)},
                }
                if validation.get("status") == "all_rejected":
                    result["reason_codes"].append("title_all_rejected")
        result["attempts"].append(summary)
    if not result["reason_codes"] and trusted:
        result["reason_codes"].append("review_required")
    result["reason_codes"] = list(dict.fromkeys(result["reason_codes"]))
    return result


def cloud_exit_code(report: dict[str, Any], child_code: int) -> int:
    # A fresh native success plus proof that publication was never attempted.
    if (
        child_code == 3 and report.get("status") in {"review_required", "no_topics"}
        and report.get("native_exit_code") == 0
        and report.get("publication_attempted") is False
        and not report.get("wechat_status")
        and report.get("run_outcome", "") in {"", "review_required", "no_topics"}
    ):
        return 0
    return child_code
