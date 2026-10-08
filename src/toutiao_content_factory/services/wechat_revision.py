"""Conservative issue tracking; trace outcomes never change a council verdict."""
from __future__ import annotations

import re
import copy
import hashlib
import json
from pathlib import Path
from difflib import SequenceMatcher

from toutiao_content_factory.services.wechat_evidence import stable_id, supporting_facts

ISSUE_FIELDS = ("hard_blockers", "fact_issues", "logic_issues", "structure_issues", "originality_issues", "compliance_issues", "required_revisions")


def verified_patch_locator(requirement: str, paragraph: str, quote: str) -> bool:
    """A shared literal anchors an unlocated request; it proves no correction."""
    if not isinstance(quote, str):
        return False
    normalized = re.sub(r"\s+", "", quote)
    return (len(re.findall(r"\w", normalized)) >= 4
            and normalized in re.sub(r"\s+", "", requirement)
            and normalized in re.sub(r"\s+", "", paragraph))


def bind_patch_locations(report, locations: list[dict], article: str, facts: list[dict]) -> None:
    """Recover exact original paragraph locations without changing the verdict."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", article.split("参考来源：", 1)[0]) if p.strip()]
    for location in locations:
        issue = next((row for row in report.issues if row["issue_id"] == location.get("issue_id")), None)
        paragraph = location.get("paragraph", "")
        if (not issue or issue["location_verified"] or paragraph not in paragraphs
                or not verified_patch_locator(issue["requirement"], paragraph, location.get("locator_quote", ""))):
            continue
        issue["paragraph"] = paragraph
        issue["location_verified"] = True
        issue["evidence_ids"] = list(dict.fromkeys(fact["fact_id"]
            for sentence in re.split(r"(?<=[。！？；])", paragraph)
            for fact in supporting_facts(sentence, facts)))


def bind_issues(report, article: str, facts: list[dict]) -> None:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", article.split("参考来源：", 1)[0]) if p.strip()]
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
                excerpts = re.findall(r"[“\"‘']([^”\"’']{8,})[”\"’']", requirement)
                quote = next((excerpt for excerpt in excerpts if re.sub(r"\s+", "", excerpt) in re.sub(r"\s+", "", article)), "")
            # A model-supplied quote is a locator only when it is in this body.
            paragraph = next((p for p in paragraphs if quote and re.sub(r"\s+", "", quote) in re.sub(r"\s+", "", p)), "")
            issue_id = stable_id("issue_", category, re.sub(r"\s+", "", requirement))
            evidence = [fact for sentence in re.split(r"(?<=[。！？；])", paragraph)
                        for fact in supporting_facts(sentence, facts)] if paragraph else []
            issues.append({"issue_id": issue_id, "category": category, "requirement": requirement,
                           "paragraph": quote if provided.get("target") == "title" else paragraph,
                           "evidence_ids": provided.get("evidence_ids", list(dict.fromkeys(f["fact_id"] for f in evidence))),
                           "verification": provided.get("verification", ""),
                           "location_verified": bool(paragraph), "target": provided.get("target", "body")})
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


def manuscript_fingerprint(article: str, title: str, fact_material: dict) -> str:
    """Bind exact text and source material, independently of layout and scores."""
    payload = {"version": 1, "article": article.replace("\r\n", "\n").strip(),
               "title": title.strip(), "facts": fact_material}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def audit_fact_material(data_report) -> dict:
    return {name: (data_report.get(name) if isinstance(data_report, dict) else getattr(data_report, name, None))
            or ([] if name in {"source_records", "fact_evidence"} else "")
            for name in ("topic_title", "article_kind", "source_records", "fact_evidence")}


def pending_verifications(content, facts: list[dict]) -> list[dict]:
    """Only requirements, real paragraph pairs and matching evidence cross the model boundary."""
    outstanding = {}
    for trace in content.revision_trace:
        for row in trace.get("issues", []):
            if isinstance(row, dict):
                if row.get("status") == "resolved" and row.get("verification"):
                    outstanding.pop(row.get("issue_id"), None)
                else:
                    outstanding[row.get("issue_id")] = row
    ids = {fact.get("fact_id"): fact for fact in facts}
    return [{key: row.get(key, "") for key in ("issue_id", "category", "requirement", "before", "after")}
            | {"evidence": [ids[key] for key in row.get("evidence_ids", []) if key in ids]}
            for row in outstanding.values()]


def _matching_evidence(quote: str, evidence_ids, facts: list[dict], *, required: bool) -> bool:
    if not isinstance(evidence_ids, list) or any(not isinstance(key, str) for key in evidence_ids):
        return False
    known = {fact["fact_id"] for fact in supporting_facts(quote, facts)}
    return (bool(evidence_ids) or not required) and set(evidence_ids).issubset(known)


def integrity_reasons(report, article: str, title: str, facts: list[dict], pending: list[dict],
                      raw: dict | None = None) -> list[str]:
    reasons = []
    if report.used_fallback or not report.is_usable():
        reasons.append("model_unavailable")
    if raw is not None and (raw.get("verdict") not in {"approved", "needs_revision", "blocked"}
                            or not str(raw.get("chief_editor_conclusion", "")).strip()):
        reasons.append("missing_model_conclusion")
    findings = report.issues
    if report.verdict == "approved" and any(getattr(report, field) for field in ISSUE_FIELDS):
        reasons.append("approval_with_unresolved_findings")
    if raw and raw.get("verdict") == "approved" and raw.get("needs_revision") is True:
        reasons.append("contradictory_conclusion")
    conclusion = str((raw or {}).get("chief_editor_conclusion", report.chief_editor_conclusion))
    if report.verdict == "approved" and re.search(r"不予通过|不可发表|不能发表|不建议发布|必须.{0,8}修改", conclusion):
        reasons.append("contradictory_conclusion")
    for issue in findings:
        target = issue.get("target", "body")
        quote = str(issue.get("paragraph", "")).strip()
        if target == "title":
            if not quote or quote not in title:
                reasons.append("invalid_title_location:" + issue["issue_id"])
        elif not quote or not issue.get("location_verified") or quote not in article.split("参考来源：", 1)[0]:
            reasons.append("missing_or_stale_location:" + issue["issue_id"])
        if not str(issue.get("verification", "")).strip():
            reasons.append("missing_finding_reason:" + issue["issue_id"])
        if not _matching_evidence(quote, issue.get("evidence_ids", []), facts,
                                  required=issue["category"] in {"fact_issues", "hard_blockers"}):
            reasons.append("invalid_finding_evidence:" + issue["issue_id"])
    evaluations = report.issue_evaluations
    for issue in pending:
        matches = [row for row in evaluations if row.get("issue_id") == issue["issue_id"]]
        if len(matches) != 1:
            reasons.append("missing_or_duplicate_verification:" + issue["issue_id"])
            continue
        row = matches[0]
        quote, reason = str(row.get("after_quote", "")).strip(), str(row.get("verification", "")).strip()
        status = row.get("status")
        if status not in {"resolved", "still_present", "unverifiable"} or not reason or not quote or quote not in article:
            reasons.append("invalid_verification:" + issue["issue_id"])
            continue
        required = issue["category"] in {"fact_issues", "hard_blockers"}
        if not _matching_evidence(quote, row.get("evidence_ids", []), facts, required=required):
            reasons.append("invalid_evidence:" + issue["issue_id"])
        if status == "resolved":
            before, after = str(issue.get("before", "")), str(issue.get("after", ""))
            meaningful = lambda text: re.sub(r"[\W_]+", "", text)
            if (not before or not after or after not in article or quote not in after
                    or meaningful(before) == meaningful(after)
                    or (issue["category"] in {"logic_issues", "structure_issues"}
                        and SequenceMatcher(None, meaningful(before), meaningful(after)).ratio() > .97)):
                reasons.append("unproven_change:" + issue["issue_id"])
            if any(item["issue_id"] == issue["issue_id"] for item in findings):
                reasons.append("resolved_but_still_required:" + issue["issue_id"])
        elif report.verdict == "approved":
            reasons.append("approval_with_unverified_issue:" + issue["issue_id"])
    if report.verdict in {"needs_revision", "blocked"} and not findings and not any(
            row.get("status") != "resolved" for row in evaluations):
        reasons.append("rejection_without_findings")
    return list(dict.fromkeys(reasons))


def apply_supplement(report, payload: dict, article: str, title: str, facts: list[dict]) -> None:
    """Repair metadata only. Every removed finding needs independent, literal proof."""
    evaluations = payload.get("finding_evaluations", [])
    if not isinstance(evaluations, list):
        raise ValueError("Missing finding evaluations")
    revised = copy.deepcopy(report.issues)
    removals = []
    for issue in revised:
        matches = [row for row in evaluations if isinstance(row, dict) and row.get("issue_id") == issue["issue_id"]]
        if len(matches) != 1:
            raise ValueError("Missing or duplicate finding evaluation")
        row = matches[0]
        quote = str(row.get("current_quote", "")).strip()
        target = row.get("target", "body")
        text = title if target == "title" else article.split("参考来源：", 1)[0]
        if (target not in {"title", "body"} or not quote or quote not in text
                or not str(row.get("verification", "")).strip()
                or not _matching_evidence(quote, row.get("evidence_ids", []), facts,
                                         required=issue["category"] in {"hard_blockers", "fact_issues"})):
            raise ValueError("Finding evaluation has no valid evidence")
        if row.get("status") == "not_applicable":
            removals.append(issue)
        elif row.get("status") == "confirmed":
            issue.update(paragraph=quote, location_verified=True, target=target,
                         verification=row["verification"], evidence_ids=row.get("evidence_ids", []))
        else:
            raise ValueError("Finding remains unverifiable")
    verdict = payload.get("verdict")
    conclusion = payload.get("chief_editor_conclusion")
    if verdict not in {"approved", "needs_revision", "blocked"} or not isinstance(conclusion, str) or not conclusion.strip():
        raise ValueError("Missing model conclusion")
    candidate = copy.deepcopy(report)
    candidate.issues = [issue for issue in revised if issue not in removals]
    for issue in removals:
        for field in ISSUE_FIELDS:
            setattr(candidate, field, [item for item in getattr(candidate, field) if item != issue["requirement"]])
    candidate.issue_evaluations = payload.get("issue_evaluations", [])
    if not isinstance(candidate.issue_evaluations, list) or any(not isinstance(row, dict) for row in candidate.issue_evaluations):
        raise ValueError("Invalid revision evaluations")
    if verdict == "approved" and any(getattr(candidate, field) for field in ISSUE_FIELDS):
        raise ValueError("Approval cannot override remaining findings")
    if candidate.hard_blockers and verdict != "blocked":
        raise ValueError("Unresolved hard blockers")
    for field in ISSUE_FIELDS:
        setattr(report, field, getattr(candidate, field))
    report.issues, report.issue_evaluations = candidate.issues, candidate.issue_evaluations
    report.verdict, report.needs_revision = verdict, verdict != "approved"
    report.chief_editor_conclusion = conclusion.strip()
    # The primary scores remain unchanged; the primary conclusion is retained in provenance.


def valid_automatic_approval(report, article: str, title: str, data_report) -> bool:
    integrity = report.audit_integrity
    model_report = integrity.get("supplement_report", integrity.get("primary_report", {}))
    return (isinstance(model_report, dict) and model_report.get("verdict") == "approved"
            and bool(str(model_report.get("chief_editor_conclusion", "")).strip())
            and report.is_usable() and not report.used_fallback and report.verdict == "approved"
            and not any(getattr(report, field) for field in ISSUE_FIELDS)
            and integrity.get("status") == "complete"
            and integrity.get("fingerprint") == manuscript_fingerprint(article, title, audit_fact_material(data_report))
            and not integrity_reasons(report, article, title, audit_fact_material(data_report)["fact_evidence"],
                                     integrity.get("pending_verifications", []), model_report))


def assert_run_automatic_approval(run_dir: Path, article: str, title: str) -> None:
    from toutiao_content_factory.models import QualityReviewReport
    report = QualityReviewReport.from_dict(json.loads((run_dir / "quality_review_council.json").read_text(encoding="utf-8-sig")))
    workflow = json.loads((run_dir / "workflow_result.json").read_text(encoding="utf-8-sig"))
    if not valid_automatic_approval(report, article, title, workflow.get("data_report", {})):
        raise ValueError("公众号审核不完整、未批准或稿件已改变，需重新审核后才能自动发表")
