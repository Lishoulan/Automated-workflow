"""Conservative issue tracking; trace outcomes never change a council verdict."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from toutiao_content_factory.services.wechat_evidence import stable_id, supporting_facts

ISSUE_FIELDS = ("hard_blockers", "fact_issues", "logic_issues", "structure_issues", "originality_issues", "compliance_issues", "required_revisions")


def bind_issues(report, article: str, facts: list[dict]) -> None:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", article) if p.strip()]
    supplied = report.issues
    issues, seen = [], set()
    for category in ISSUE_FIELDS:
        for requirement in getattr(report, category):
            if requirement in seen:
                continue
            seen.add(requirement)
            provided = next((i for i in supplied if i.get("requirement") == requirement), {})
            quote = str(provided.get("paragraph", "")).strip()
            if not quote:
                excerpts = re.findall(r"[“\"‘]([^”\"’]{8,})[”\"’]", requirement)
                quote = next((excerpt for excerpt in excerpts if re.sub(r"\s+", "", excerpt) in re.sub(r"\s+", "", article)), "")
            # A model-supplied quote is a locator only when it is in this body.
            paragraph = next((p for p in paragraphs if quote and re.sub(r"\s+", "", quote) in re.sub(r"\s+", "", p)), "")
            issue_id = stable_id("issue_", category, re.sub(r"\s+", "", requirement))
            evidence = [fact for sentence in re.split(r"(?<=[。！？；])", paragraph)
                        for fact in supporting_facts(sentence, facts)] if paragraph else []
            issues.append({"issue_id": issue_id, "category": category, "requirement": requirement,
                           "paragraph": paragraph, "evidence_ids": list(dict.fromkeys(f["fact_id"] for f in evidence)),
                           "location_verified": bool(paragraph)})
    report.issues = issues


def begin_revision(report, before: str, after: str, round_number: int) -> dict:
    before_paragraphs = [p.strip() for p in re.split(r"\n\s*\n", before) if p.strip()]
    after_paragraphs = [p.strip() for p in re.split(r"\n\s*\n", after) if p.strip()]
    entries = []
    for issue in report.issues:
        old = issue["paragraph"]
        new = ""
        if old:
            if old in after_paragraphs:
                new = old
            else:
                match = max(after_paragraphs, key=lambda p: SequenceMatcher(None, old, p).ratio(), default="")
                if match and SequenceMatcher(None, old, match).ratio() >= .35:
                    new = match
        entries.append({**issue, "before": old, "after": new,
                        "status": "still_present" if old and new == old else "unverifiable",
                        "verification": "", "body_changed": before != after})
    return {"revision_round": round_number, "issues": entries,
            "before_hash": stable_id("body_", before), "after_hash": stable_id("body_", after)}


def finish_revision(trace: dict, report, after: str, facts: list[dict]) -> None:
    evaluations = {item.get("issue_id"): item for item in report.issue_evaluations if isinstance(item, dict)}
    remaining = {item["issue_id"] for item in report.issues}
    for issue in trace["issues"]:
        evaluation = evaluations.get(issue["issue_id"], {})
        status = evaluation.get("status")
        quote = str(evaluation.get("after_quote", "")).strip()
        reason = str(evaluation.get("verification", "")).strip()
        if issue["issue_id"] in remaining or status == "still_present":
            issue["status"] = "still_present"
            continue
        if issue["before"] and issue["before"] == issue["after"]:
            issue["status"] = "still_present"
            continue
        # Omission, superficial wording change, approval, and invented quotes
        # are all insufficient to prove a specific correction.
        if (status != "resolved" or report.used_fallback or not issue["body_changed"]
                or not quote or quote not in after or not reason or not issue["location_verified"]):
            continue
        evidence_ids = evaluation.get("evidence_ids", [])
        known = {item["fact_id"] for item in supporting_facts(quote, facts)}
        if issue["category"] in {"fact_issues", "hard_blockers"} and (not evidence_ids or not set(evidence_ids).issubset(known)):
            continue
        issue.update(status="resolved", after=quote, verification=reason)
