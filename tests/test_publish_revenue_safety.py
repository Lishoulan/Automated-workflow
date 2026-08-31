from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from toutiao_content_factory.services.publish_service import PublishService


def _prepare_approved_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    publish_dir = run_dir / "publish"
    publish_dir.mkdir(parents=True)
    (run_dir / "review_status.json").write_text(
        json.dumps({"status": "approved", "selected_title": "储能订单兑现"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (publish_dir / "publish_meta.json").write_text(
        json.dumps(
            {
                "selected_title": "储能订单兑现",
                "selected_topic": {
                    "title": "储能订单释放",
                    "url": "https://example.test/topic",
                    "commercial_intent": "high",
                    "content_mix": "conversion",
                    "cta_variant": "consultation",
                    "cta_source_code": "storage_high_consultation",
                    "title_variant": "data_hook",
                    "cover_variant": "data_anchor",
                    "platform_variant": "recommendation_feed",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (publish_dir / "article.md").write_text("数据表明，储能订单正在兑现。" * 300, encoding="utf-8")
    return run_dir


def _make_service(app_settings, monkeypatch) -> PublishService:
    object.__setattr__(app_settings.publishing, "enabled", True)
    service = PublishService(app_settings, logging.getLogger("publish-safety-test"))
    monkeypatch.setattr(
        service,
        "_collect_preflight_summary_locked",
        lambda: {"blocking_flags": [], "warnings": [], "diagnostics": {}},
    )
    return service


def test_failed_quality_gate_blocks_real_publish(app_settings, tmp_path: Path, monkeypatch) -> None:
    service = _make_service(app_settings, monkeypatch)
    run_dir = _prepare_approved_run(tmp_path)
    monkeypatch.setattr(
        service.content_guard_service,
        "build_publish_guard_report",
        lambda _run_dir: {
            "blocking_flags": [],
            "warnings": [],
            "quality_check": {"is_passed": False, "issues": ["字数不足"]},
        },
    )
    monkeypatch.setattr(service, "_publish_to_toutiao", lambda **_kwargs: pytest.fail("must not publish"))

    with pytest.raises(RuntimeError, match="quality gate blocked"):
        service._publish_run_locked(run_dir)



def test_failed_quality_gate_still_allows_draft_mode(app_settings, tmp_path: Path, monkeypatch) -> None:
    service = _make_service(app_settings, monkeypatch)
    object.__setattr__(app_settings.publishing, "draft_only", True)
    run_dir = _prepare_approved_run(tmp_path)
    monkeypatch.setattr(
        service.content_guard_service,
        "build_publish_guard_report",
        lambda _run_dir: {
            "blocking_flags": [],
            "warnings": [],
            "quality_check": {"is_passed": False, "issues": ["字数不足"]},
        },
    )
    calls: list[dict] = []

    def fake_publish(**kwargs):
        calls.append(kwargs)
        return {"status": "draft_saved"}

    monkeypatch.setattr(service, "_publish_to_toutiao", fake_publish)

    service._publish_run_locked(run_dir)

    assert calls and calls[0]["draft_only"] is True


def test_short_article_does_not_raise_undefined_notes_error(
    app_settings, tmp_path: Path, monkeypatch
) -> None:
    service = _make_service(app_settings, monkeypatch)
    run_dir = _prepare_approved_run(tmp_path)
    (run_dir / "publish" / "article.md").write_text("短文。", encoding="utf-8")
    monkeypatch.setattr(
        service.content_guard_service,
        "build_publish_guard_report",
        lambda _run_dir: {
            "blocking_flags": [],
            "warnings": [],
            "quality_check": {"is_passed": True, "issues": []},
        },
    )
    monkeypatch.setattr(service.precheck_service, "run_precheck", lambda *_args, **_kwargs: {"status": "passed"})
    monkeypatch.setattr(
        service,
        "_publish_to_toutiao",
        lambda **_kwargs: {"status": "submitted_for_review", "external_url": "https://example.test/article"},
    )

    result = service._publish_run_locked(run_dir)

    assert result["publish_result"].exists()
    payload = json.loads(result["publish_result"].read_text(encoding="utf-8"))
    assert payload["status"] == "submitted_for_review"
    assert any("字数不足1500字" in item for item in payload["preflight_summary"]["warnings"])


def test_toutiao_final_sanitizer_removes_external_source_metadata(
    app_settings, tmp_path: Path, monkeypatch
) -> None:
    service = _make_service(app_settings, monkeypatch)
    article = (
        "机器人前瞻（公众号：robot_pro） 作者 | 钟宸编辑 | 漠影看着提速，实则分化。\n\n"
        "关注我。聚焦科技商业、AI产业与先进制造。\n\n"
        "#机器人 #具身智能 #AI"
    )

    cleaned = service._sanitize_article_for_publish(article)

    assert "看着提速，实则分化" in cleaned
    assert "机器人前瞻" not in cleaned
    assert "robot_pro" not in cleaned
    assert "作者" not in cleaned
    assert "编辑" not in cleaned
    assert "关注我" not in cleaned
    assert "#机器人" not in cleaned


def test_content_guard_block_is_not_bypassed_by_publishable_platform(
    app_settings, tmp_path: Path, monkeypatch
) -> None:
    service = _make_service(app_settings, monkeypatch)
    run_dir = _prepare_approved_run(tmp_path)
    monkeypatch.setattr(
        service,
        "_collect_preflight_summary_locked",
        lambda: {
            "blocking_flags": ["platform diagnostics incomplete"],
            "warnings": [],
            "current_url": "https://mp.toutiao.com/profile_v4/graphic/publish",
            "diagnostics": {"summary": {"creator_name": "测试账号"}},
        },
    )
    monkeypatch.setattr(
        service.content_guard_service,
        "build_publish_guard_report",
        lambda _run_dir: {
            "blocking_flags": ["Content is too similar to a recently published run."],
            "warnings": [],
            "quality_check": {"is_passed": True, "issues": []},
        },
    )

    with pytest.raises(RuntimeError, match="content guard blocked"):
        service._publish_run_locked(run_dir)


def test_publish_completion_registers_revenue_identity_from_meta_topic(
    app_settings, tmp_path: Path, monkeypatch
) -> None:
    service = _make_service(app_settings, monkeypatch)
    run_dir = _prepare_approved_run(tmp_path)
    (run_dir / "workflow_result.json").write_text(
        json.dumps({"estimated_cost": 1.25}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        service.content_guard_service,
        "build_publish_guard_report",
        lambda _run_dir: {
            "blocking_flags": [],
            "warnings": [],
            "quality_check": {"is_passed": True, "issues": []},
        },
    )
    monkeypatch.setattr(service, "_publish_to_toutiao", lambda **_kwargs: {"status": "submitted_for_review"})
    monkeypatch.setattr(service.precheck_service, "run_precheck", lambda *_args, **_kwargs: {"status": "passed"})

    result = service._publish_run_locked(run_dir)

    assert result["content_id"].startswith("ci_")
    content_index = json.loads(Path(result["content_index"]).read_text(encoding="utf-8"))
    identity = content_index[result["content_id"]]
    assert identity["topic_title"] == "储能订单释放"
    assert identity["commercial_intent"] == "high"
    assert identity["content_mix"] == "conversion"
    assert identity["cta_variant"] == "consultation"
    cost_ledger = json.loads(Path(result["cost_ledger"]).read_text(encoding="utf-8"))
    assert cost_ledger[result["content_id"]]["api_cost"] == 1.25
