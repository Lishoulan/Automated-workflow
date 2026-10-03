"""渠道卫生：微头条选题边界、微信采集开关、草稿开关命名、规则文件分叉。

对应 2026-09-16 盘出的 A 类遗留项，每条都有当时的实况依据：
- 兜底选题池漏了账号边界过滤 → 机器人号发了「PS5模拟器突破！…」；
- 微信渠道停用后采集仍每次失败 → 纯噪音；
- `TOUTIAO_DRAFT_ONLY` 与 `TOUTIAO_PUBLISH_DRAFT_ONLY` 名字像、语义不同；
- `config/best_practices_rule.json` 是运行时产物却被 git 跟踪 → 两个通道规则静默分叉。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

from toutiao_content_factory.agents.base import BaseAgent
from toutiao_content_factory.models import TopicCandidate
from toutiao_content_factory.services.weitoutiao_generator import WeiToutiaoStandaloneGenerator


def _topic(title: str, *, rank: int = 1, score: int = 10) -> TopicCandidate:
    return TopicCandidate(
        title=title,
        url=f"https://example.com/topic",
        source="test",
        source_type="test",
        rank=rank,
        total_score=score,
    )


# ---------------------------------------------------------------------------
# ② 兜底选题池必须过账号边界
# ---------------------------------------------------------------------------


def test_standalone_weitoutiao_fallback_pool_applies_account_boundary_filter() -> None:
    """兜底池以前只做「今日已用去重」，漏了 `filter_topics_for_account`。

    实况：机器人号从常青池发出「PS5模拟器突破！KytyPS5可运行《宇宙机器人》」，
    而同一个题刚被主流程以 insufficient_source_facts 跳过。
    """
    off_brand = _topic("PS5游戏机模拟器KytyPS5新进展")
    on_brand = _topic("灵巧手产业交付进度", rank=2)
    calls: list[list[str]] = []

    def filter_topics_for_account(topics):
        calls.append([item.title for item in topics])
        return [item for item in topics if "PS5" not in item.title]

    automation = SimpleNamespace(
        _load_daily_topics=lambda: SimpleNamespace(topics=[]),
        _available_topics=lambda topics: list(topics),
        filter_topics_for_account=filter_topics_for_account,
        topic_pool_service=SimpleNamespace(
            build_fallback_report=lambda: SimpleNamespace(topics=[off_brand, on_brand]),
        ),
    )
    generator = WeiToutiaoStandaloneGenerator.__new__(WeiToutiaoStandaloneGenerator)
    generator.settings = SimpleNamespace(publishing=SimpleNamespace(weitoutiao_personas=["hot_comment"]))
    generator.logger = logging.getLogger("test")
    generator.topic_automation = automation

    selected = generator.select_topics(count=3)

    assert [item.title for item in selected] == [on_brand.title]
    # 兜底池确实经过了账号边界过滤（这正是修复点）。
    assert any(any("PS5" in title for title in titles) for titles in calls)


# ---------------------------------------------------------------------------
# ③ 规则文件分叉：指纹可见 + 差异告警
# ---------------------------------------------------------------------------


def _rule_agent(tmp_path: Path) -> BaseAgent:
    project_root = tmp_path / "project"
    (project_root / "config").mkdir(parents=True, exist_ok=True)
    agent = BaseAgent.__new__(BaseAgent)
    agent.settings = SimpleNamespace(project_root=project_root)
    agent.logger = logging.getLogger("test")
    return agent


def _write_rules(agent: BaseAgent, rules: list[dict]) -> None:
    agent.best_practices_rule_json_path().write_text(
        json.dumps({"rule_count": len(rules), "generated_at": "2026-09-16", "rules": rules}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_best_practices_identity_is_logged_once_per_process(tmp_path, caplog) -> None:
    agent = _rule_agent(tmp_path)
    _write_rules(agent, [{"rule_id": "R001", "title": "x"}])
    with caplog.at_level(logging.INFO):
        agent.read_best_practices_rules_json()
        agent.read_best_practices_rules_json()
    identity_lines = [record.message for record in caplog.records if "Best-practices rules in effect" in record.message]
    assert len(identity_lines) == 1
    assert "sha256=" in identity_lines[0]
    assert "count=1" in identity_lines[0]


def test_best_practices_drift_check_degrades_when_git_is_unavailable(tmp_path, monkeypatch) -> None:
    agent = _rule_agent(tmp_path)
    _write_rules(agent, [{"rule_id": "R001"}])
    monkeypatch.setattr(
        "toutiao_content_factory.agents.base.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("git missing")),
    )
    # 拿不到 git 信息时返回空串（视为「不知道」），不能抛异常，也不能谎报有差异。
    assert agent._best_practices_rules_drift_from_head() == ""


# ---------------------------------------------------------------------------
# ④ 草稿开关命名：只设旧名时要告警
# ---------------------------------------------------------------------------


def test_legacy_draft_only_env_warns_when_canonical_is_absent(monkeypatch, caplog) -> None:
    from toutiao_content_factory import config

    monkeypatch.setenv("TOUTIAO_DRAFT_ONLY", "1")
    monkeypatch.delenv("TOUTIAO_PUBLISH_DRAFT_ONLY", raising=False)
    with caplog.at_level(logging.WARNING):
        config._warn_legacy_draft_only_env()
    assert any("TOUTIAO_DRAFT_ONLY" in record.message for record in caplog.records)


def test_legacy_draft_only_env_is_silent_when_canonical_is_set(monkeypatch, caplog) -> None:
    from toutiao_content_factory import config

    monkeypatch.setenv("TOUTIAO_DRAFT_ONLY", "1")
    monkeypatch.setenv("TOUTIAO_PUBLISH_DRAFT_ONLY", "1")
    with caplog.at_level(logging.WARNING):
        config._warn_legacy_draft_only_env()
    assert not [record for record in caplog.records if "TOUTIAO_DRAFT_ONLY" in record.message]


def test_legacy_draft_only_env_is_silent_when_unset(monkeypatch, caplog) -> None:
    from toutiao_content_factory import config

    monkeypatch.delenv("TOUTIAO_DRAFT_ONLY", raising=False)
    monkeypatch.delenv("TOUTIAO_PUBLISH_DRAFT_ONLY", raising=False)
    with caplog.at_level(logging.WARNING):
        config._warn_legacy_draft_only_env()
    assert not caplog.records
