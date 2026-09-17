"""Phase 1 标题链路：D1 三层池隔离 / D2 all-rejected 是真失败 / D3 三来源同一校验入口。

这三个缺陷是一条链上的三处，单修任何一处都不够：

* **D3 校验不对称** —— `_fallback_fact_titles()` 曾用
  `validate_title_compliance(title)`（**不带正文**）预筛，于是兜底标题跳过了
  「标题-正文一致性」与「具象锚点承接」。同一个标题走模型路径被拒、走兜底路径能活。
* **D1 混池排名** —— 模型 20 条与兜底 22~25 条合并进同一个 `_rank_titles()`
  竞争池。兜底条数更多、又带公司/数字锚定，靠数量和权重就能抢掉模型候选；
  于是「候选池里到底有多少是模型产出的」这件事根本无法从结果反推。
* **D2 静默绕过** —— `if compliant_titles:` 的 else 分支会**保留全部未过滤候选**；
  `_filter_titles_by_quality()` 会**保底塞回问题最少的那一条**；
  `_fallback_title_for()` 末尾还有 `or candidates[0]`。三处叠起来，
  「一个合格标题都没有」这个事实永远不会暴露，闸门退化成排序器。

本文件按这三条组织，用例按 D1 Case A–D / D2 Case 1–4 编号，与任务书一致。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from toutiao_content_factory.agents.title_agent import (
    TITLE_SOURCE_EMERGENCY,
    TITLE_SOURCE_FALLBACK,
    TITLE_SOURCE_MODEL,
    TITLE_STATUS_ALL_REJECTED,
    TITLE_STATUS_PARTIAL,
    TITLE_STATUS_PASSED,
    REASON_VALIDATION_CONTEXT_UNAVAILABLE,
    TitleAgent,
)
from toutiao_content_factory.models import (
    CacheContext,
    DataReport,
    GeneratedContent,
    TrendReport,
)
from toutiao_content_factory.services.prompt_repository import PromptRepository


# ---------------------------------------------------------------------------
# 夹具素材：正文与「过闸 / 不过闸」的标题
#
# 这些标题的判定都是**实测**出来的（用真实 config.yaml 的 title_max_length=30
# 跑 validate_title_candidate），不是照着逻辑推的：
#   `比亚迪的交付观察`   → rule_length（<10 字）
#   `产业链利润重新分配，谁先兑现` → missing_concrete_hook + rule_006 + rule_009
# ---------------------------------------------------------------------------

BODY = "公开材料显示，比亚迪提出460万台目标，同比增长30%，交付节奏仍是关键。"

VALID_TITLES = [
    "比亚迪定下460万台目标，交付才是关键",
    "比亚迪460万台目标之后，交付才是真正的分水岭",
    "比亚迪目标460万台，交付验证决定成败",
]

INVALID_TITLES = [
    "人形机器人利润流向，正从整机转零部件",   # missing_concrete_hook + rule_009
    "产业链利润重新分配，谁先兑现",           # hook FAIL + rule_006 + rule_009
    "比亚迪的交付观察",                       # rule_length
]


def _agent(app_settings, mock_openai_service, logger) -> TitleAgent:
    return TitleAgent(
        app_settings,
        PromptRepository(app_settings),
        mock_openai_service,
        logger,
    )


def _report() -> DataReport:
    return DataReport(
        topic_title="比亚迪玩跨界？进军人形机器人，代号尧舜禹",
        focus_industry="AI机器人",
        focus_keywords=["比亚迪", "人形机器人"],
        supply_chain_keywords=["比亚迪", "人形机器人"],
        growth_numbers=["460万台"],
        companies=["比亚迪"],
        focus_companies=["比亚迪"],
        core_data=["比亚迪定下460万台目标"],
        evidence_notes=["公开材料：比亚迪进军人形机器人，提出460万台目标"],
    )


def _content(body: str = BODY) -> GeneratedContent:
    return GeneratedContent(article=body, polished_article=body, summary=body[:60])


def _stub_run(agent: TitleAgent, monkeypatch, *, model_payload, fallback, emergency):
    """把 `run()` 的外部依赖打桩，只留下标题池逻辑本身。"""
    monkeypatch.setattr(agent, "render_prompt", lambda *a, **k: "")
    monkeypatch.setattr(agent, "best_practices_instruction", lambda *a, **k: "")
    monkeypatch.setattr(
        agent,
        "_build_title_playbook_context",
        lambda *a, **k: {
            "track": "AI机器人",
            "top_keywords": [],
            "title_templates": [],
            "topic_imitation_pool": [],
            "example_titles": [],
            "note": "",
        },
    )
    monkeypatch.setattr(agent, "request_json", lambda *a, **k: model_payload)
    monkeypatch.setattr(agent, "_fallback_titles", lambda *a, **k: list(fallback))
    monkeypatch.setattr(agent, "_emergency_titles", lambda *a, **k: list(emergency))


def _run(agent: TitleAgent) -> dict:
    return agent.run(
        _content(),
        TrendReport(),
        _report(),
        agent.settings.style_profiles[agent.settings.default_style],
        CacheContext(),
    )


# ---------------------------------------------------------------------------
# D3：三类来源必须进入**同一个**正式校验入口，并且都拿到正文
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", [TITLE_SOURCE_MODEL, TITLE_SOURCE_FALLBACK, TITLE_SOURCE_EMERGENCY])
@pytest.mark.parametrize("title", [*VALID_TITLES, *INVALID_TITLES])
def test_same_title_gives_the_same_verdict_in_every_pool(
    app_settings, mock_openai_service, logger, monkeypatch, source, title
) -> None:
    """D3：同一个标题，走哪个来源池都必须得到**完全相同**的判定。

    这是校验对称性的回归锁 —— 旧实现里 `_fallback_fact_titles()` 用不带正文的
    校验预筛，同一个标题在模型池被判 `title_body_inconsistency`、在兜底池却能活。
    """
    agent = _agent(app_settings, mock_openai_service, logger)

    baseline = agent.validate_title_candidate(title, BODY)
    pool = agent._build_title_pool(source, [title], BODY, _report(), [])

    outcome = (pool.valid_titles == [title]), tuple(
        code for item in pool.rejected for code in item.reason_codes
    )
    expected = (baseline.passed, baseline.reason_codes)
    assert outcome == expected


def test_pool_verdict_does_not_depend_on_the_source_label(
    app_settings, mock_openai_service, logger
) -> None:
    """三个池喂同一批标题 ⇒ 合格集与拒绝原因必须逐项一致。"""
    agent = _agent(app_settings, mock_openai_service, logger)
    titles = [*VALID_TITLES, *INVALID_TITLES]

    results = {}
    for source in (TITLE_SOURCE_MODEL, TITLE_SOURCE_FALLBACK, TITLE_SOURCE_EMERGENCY):
        pool = agent._build_title_pool(source, titles, BODY, _report(), [])
        results[source] = (
            tuple(sorted(pool.valid_titles)),
            tuple(sorted(item.title for item in pool.rejected)),
            tuple(sorted(pool.reason_summary().items())),
        )

    assert len(set(results.values())) == 1, results
    assert list(results[TITLE_SOURCE_MODEL][0]) == sorted(VALID_TITLES)
    assert list(results[TITLE_SOURCE_MODEL][1]) == sorted(INVALID_TITLES)


def test_fallback_pool_is_body_aware(app_settings, mock_openai_service, logger) -> None:
    """D3：兜底池必须吃正文 —— 标题提到正文里没有的公司就要被拒。"""
    agent = _agent(app_settings, mock_openai_service, logger)
    # 正文只讲比亚迪；标题却点名宁德时代 ⇒ 标题-正文不一致。
    pool = agent._build_title_pool(
        TITLE_SOURCE_FALLBACK,
        ["宁德时代定下460万台目标，交付才是关键"],
        BODY,
        _report(),
        [],
    )
    assert pool.valid_titles == []
    assert any(
        "title_body_inconsistency" in item.reason_codes for item in pool.rejected
    ), pool.reason_summary()


@pytest.mark.parametrize(
    "title",
    [
        "宁德时代定下460万台目标，交付才是关键",     # 标题公司名不存在正文
        "某公司定下460万台目标，交付才是关键",       # 标题主体与正文不符（材料主体是比亚迪）
        "产业链利润重新分配，谁先兑现",               # 抽象主体 + 未承接锚点
        "比亚迪的交付观察",                           # 过短
    ],
)
@pytest.mark.parametrize("source", [TITLE_SOURCE_MODEL, TITLE_SOURCE_FALLBACK, TITLE_SOURCE_EMERGENCY])
def test_inconsistent_titles_are_rejected_on_every_pool(
    app_settings, mock_openai_service, logger, source, title
) -> None:
    """D3 必需覆盖：主体不一致 / 主体与正文不符 / 抽象主体 / 长度不足，三池一律拒绝。"""
    agent = _agent(app_settings, mock_openai_service, logger)

    pool = agent._build_title_pool(source, [title], BODY, _report(), [])

    assert pool.valid_titles == [], f"{title!r} 在 {source} 池不该通过：{pool.reason_summary()}"
    assert pool.rejected


def test_correct_title_passes_on_every_pool(app_settings, mock_openai_service, logger) -> None:
    """D3 必需覆盖：正文正常且标题正确 —— 三池都必须放行（对称性不能靠「都拒」来满足）。"""
    agent = _agent(app_settings, mock_openai_service, logger)

    for source in (TITLE_SOURCE_MODEL, TITLE_SOURCE_FALLBACK, TITLE_SOURCE_EMERGENCY):
        pool = agent._build_title_pool(source, [VALID_TITLES[0]], BODY, _report(), [])
        assert pool.valid_titles == [VALID_TITLES[0]], source


def test_title_number_absent_from_body_is_only_a_suggestion(
    app_settings, mock_openai_service, logger
) -> None:
    """已知缺口（如实记录，未修）：标题里的数字**不在**正文时只记为建议、不拦。

    `_check_title()` 对「标题数字未在正文出现」走的是 `suggestions`，不是 `issues`；
    而 `validate_title_candidate()` 只按 `issues` 判定。所以
    「比亚迪定下999万台目标...」会因为**公司名在正文**而通过具象锚点闸门。
    这是当前有意保留的取舍（数字常有四舍五入/换算是写法差异，硬拦会误杀），
    列在这里是为了让下一个改这块的人知道它是有意为之，而不是漏了。
    """
    agent = _agent(app_settings, mock_openai_service, logger)

    outcome = agent.validate_title_candidate("比亚迪定下999万台目标，交付才是关键", BODY)

    assert outcome.passed is True
    assert outcome.concrete_hook_status == "PASS"   # 命中的是正文里的 `比亚迪`


def test_validation_context_unavailable_is_recorded_not_swallowed(
    app_settings, mock_openai_service, logger
) -> None:
    """正文缺失时记 `validation_context_unavailable`，**不**静默当成「校验通过」。"""
    agent = _agent(app_settings, mock_openai_service, logger)
    pool = agent._build_title_pool(TITLE_SOURCE_MODEL, [VALID_TITLES[0]], "", _report(), [])

    assert pool.valid_titles == []
    assert pool.unavailable
    assert pool.reason_summary().get(REASON_VALIDATION_CONTEXT_UNAVAILABLE) == 1


def test_missing_body_still_enforces_the_body_independent_rules(
    app_settings, mock_openai_service, logger
) -> None:
    """没有正文 ≠ 什么都放行：长度/禁用句式这类与正文无关的规则照样生效。"""
    agent = _agent(app_settings, mock_openai_service, logger)
    too_long = "比亚迪" + "定下460万台目标交付才是关键" * 4
    discouraged = "比亚迪460万台目标之后，利润先流向谁"

    long_result = agent.validate_title_candidate(too_long, "")
    discouraged_result = agent.validate_title_candidate(discouraged, "")

    assert long_result.passed is False and "title_too_long" in long_result.reason_codes
    assert discouraged_result.passed is False
    assert "discouraged_pattern" in discouraged_result.reason_codes


def test_internal_instruction_leak_is_rejected_on_every_pool(
    app_settings, mock_openai_service, logger
) -> None:
    """内部流程指令泄漏进标题 ⇒ 一律拒绝（所有来源池同判）。

    实测来源：随机 30 条最终选中标题里有 3 条是
    **「适合写成人形机器人入场，AI谁最紧张」** —— 选题元数据的「体裁要求：
    适合写成人形机器人项目或订单拆解稿…」被当成关键词用进了标题。
    它能过 RULE_006（含「人形机器人」）和 RULE_009（含「入场」），
    所以必须在标题闸门里单独拦。
    """
    agent = _agent(app_settings, mock_openai_service, logger)

    for source in (TITLE_SOURCE_MODEL, TITLE_SOURCE_FALLBACK, TITLE_SOURCE_EMERGENCY):
        outcome = agent.validate_title_candidate(
            "适合写成人形机器人入场，AI谁最紧张", BODY
        )
        assert outcome.passed is False, source
        assert "internal_instruction_leak" in outcome.reason_codes


# ---------------------------------------------------------------------------
# D1：三层池，池间不混合排名
# ---------------------------------------------------------------------------


def test_d1_case_a_model_wins_even_when_fallback_looks_more_clickable(
    app_settings, mock_openai_service, logger, monkeypatch
) -> None:
    """D1 Case A：模型池有合格候选 ⇒ 只用模型池，兜底**根本不参与竞争**。"""
    agent = _agent(app_settings, mock_openai_service, logger)
    # 兜底给 10 条合格、且句式上更像高点击标题；模型只有 3 条。
    fallback = list(VALID_TITLES) + [f"比亚迪460万台之后，第{i}个变量才是关键" for i in range(1, 8)]
    _stub_run(
        agent,
        monkeypatch,
        model_payload={"titles": list(VALID_TITLES)},
        fallback=fallback,
        emergency=["比亚迪460万台目标，交付验证决定成败"],
    )

    result = _run(agent)

    assert result["selected_title_source"] == TITLE_SOURCE_MODEL
    assert result["titles"], result["title_validation"]
    # 兜底池**根本没被构建** —— 这才是「不混池」的硬证据，
    # 而不是「混池之后模型碰巧赢了」。
    assert result["title_validation"]["pools"]["fallback"] is None
    fallback_only = set(fallback) - set(VALID_TITLES)
    assert fallback_only & set(result["titles"]) == set()
    assert result["title_validation"]["pools"]["model"]["reject_count"] == 0
    assert result["title_validation"]["status"] == TITLE_STATUS_PASSED


def test_d1_case_b_fallback_rescues_only_when_the_model_pool_is_empty(
    app_settings, mock_openai_service, logger, monkeypatch
) -> None:
    """D1 Case B：模型池 0 条合格 ⇒ 启用兜底救援池。"""
    agent = _agent(app_settings, mock_openai_service, logger)
    _stub_run(
        agent,
        monkeypatch,
        model_payload={"titles": list(INVALID_TITLES)},
        fallback=list(VALID_TITLES),
        emergency=[],
    )

    result = _run(agent)

    assert result["selected_title_source"] == TITLE_SOURCE_FALLBACK
    assert set(result["titles"]) <= set(VALID_TITLES)
    assert result["title_validation"]["pools"]["model"]["passed_count"] == 0
    assert result["title_validation"]["status"] == TITLE_STATUS_PASSED


def test_d1_case_c_emergency_is_the_last_resort(
    app_settings, mock_openai_service, logger, monkeypatch
) -> None:
    """D1 Case C：模型 0 / 兜底 0 ⇒ 才轮到应急池，且它同样要过正式校验。"""
    agent = _agent(app_settings, mock_openai_service, logger)
    _stub_run(
        agent,
        monkeypatch,
        model_payload={"titles": list(INVALID_TITLES)},
        fallback=list(INVALID_TITLES),
        emergency=list(VALID_TITLES),
    )

    result = _run(agent)

    assert result["selected_title_source"] == TITLE_SOURCE_EMERGENCY
    assert set(result["titles"]) <= set(VALID_TITLES)
    assert result["title_validation"]["pools"]["fallback"]["passed_count"] == 0


def test_d1_case_d_all_pools_empty_is_a_real_failure(
    app_settings, mock_openai_service, logger, monkeypatch
) -> None:
    """D1 Case D / D2 Case 4：三层全灭 ⇒ 交回 topic attempt loop，**不**放行未校验标题。"""
    agent = _agent(app_settings, mock_openai_service, logger)
    _stub_run(
        agent,
        monkeypatch,
        model_payload={"titles": list(INVALID_TITLES)},
        fallback=list(INVALID_TITLES),
        emergency=list(INVALID_TITLES),
    )

    result = _run(agent)

    assert result["titles"] == []
    assert result["recommended_titles"] == []
    assert result["title_a"] == "" and result["title_b"] == ""
    assert result["selected_title_source"] == ""
    assert result["title_validation"]["status"] == TITLE_STATUS_ALL_REJECTED
    assert set(result["title_validation"]["reason_summary"]) & {
        "missing_concrete_hook",
        "rule_006_abstract_subject",
        "rule_009_low_information",
    }


def test_d1_emergency_titles_are_validated_not_admitted_raw(
    app_settings, mock_openai_service, logger, monkeypatch
) -> None:
    """D1/§47.5：应急池**没有**免检特权 —— 不合格的应急标题不能进最终列表。"""
    agent = _agent(app_settings, mock_openai_service, logger)
    _stub_run(
        agent,
        monkeypatch,
        model_payload={"titles": list(INVALID_TITLES)},
        fallback=list(INVALID_TITLES),
        emergency=["临时应急标题", *VALID_TITLES],
    )

    result = _run(agent)

    assert "临时应急标题" not in result["titles"]
    assert result["title_validation"]["pools"]["emergency"]["reject_count"] >= 1


# ---------------------------------------------------------------------------
# D2：all-rejected 是可观测的真失败
# ---------------------------------------------------------------------------


def test_d2_case_1_partial_pass_keeps_only_the_valid_titles(
    app_settings, mock_openai_service, logger
) -> None:
    """D2 Case 1：10 条候选 8 过 2 拒 ⇒ 只留 8 条，status=partial。"""
    agent = _agent(app_settings, mock_openai_service, logger)
    titles = list(VALID_TITLES) + list(INVALID_TITLES)

    pool = agent._build_title_pool(TITLE_SOURCE_MODEL, titles, BODY, _report(), [])

    assert pool.status == TITLE_STATUS_PARTIAL
    assert len(pool.valid_titles) == len(VALID_TITLES)
    assert len(pool.rejected) == len(INVALID_TITLES)
    assert set(pool.valid_titles).isdisjoint(INVALID_TITLES)


def test_d2_case_2_all_rejected_reports_status_and_reasons(
    app_settings, mock_openai_service, logger
) -> None:
    """D2 Case 2：0 过 10 拒 ⇒ 0 合格 + status=all_rejected + 可读原因。"""
    agent = _agent(app_settings, mock_openai_service, logger)
    titles = list(INVALID_TITLES) * 4   # 12 条

    pool = agent._build_title_pool(TITLE_SOURCE_MODEL, titles, BODY, _report(), [])

    assert pool.valid_titles == []
    assert pool.status == TITLE_STATUS_ALL_REJECTED
    summary = pool.to_dict()
    assert summary["passed_count"] == 0
    assert summary["reject_count"] == len(titles)
    assert summary["top_reasons"], summary
    # 上层必须能回答「为什么一个标题都没有」
    assert all(isinstance(code, str) and code for code, _ in summary["top_reasons"])
    assert all(count > 0 for _, count in summary["top_reasons"])


def test_d2_no_branch_restores_unvalidated_candidates(
    app_settings, mock_openai_service, logger, monkeypatch
) -> None:
    """D2 的禁令本身：全灭时既不复原候选，也不回退到「问题最少的一条」。"""
    agent = _agent(app_settings, mock_openai_service, logger)
    _stub_run(
        agent,
        monkeypatch,
        model_payload={"titles": list(INVALID_TITLES)},
        fallback=[],
        emergency=[],
    )

    result = _run(agent)

    assert result["titles"] == []
    for bad in INVALID_TITLES:
        assert bad not in result["titles"]
        assert bad not in result["recommended_titles"]


def test_d2_selection_layer_reports_all_rejected(app_settings, tmp_path: Path) -> None:
    """D2 Case 3：最终选择层同样把「一个都没有」报成 all_rejected（topic loop 据此换选题）。"""
    from toutiao_content_factory.services.review_service import ReviewWorkflowService

    service = ReviewWorkflowService(app_settings, logging.getLogger("test_d2_selection"))
    service._title_candidates = lambda bundle: ["临时应急标题", "产业链利润重新分配，谁先兑现"]
    service._log_title_skeleton_collapse = lambda *a, **k: None

    bundle = type("Bundle", (), {"article": BODY, "titles": [], "recommended_titles": []})()
    service._load_bundle = lambda run_dir: (bundle, None)

    selection = service.select_auto_title(tmp_path)

    assert selection["selected_title"] == ""
    assert selection["status"] == "all_rejected"
    assert selection["passed_count"] == 0
    assert selection["candidate_count"] == 2
    assert selection["reject_count"] == 2
    assert selection["top_reasons"], selection


def test_d2_selection_layer_still_returns_a_passing_title(app_settings, tmp_path: Path) -> None:
    """D2 的反向锁：候选里只要有合格的，选择层必须照常返回（全灭判定不能误伤）。"""
    from toutiao_content_factory.services.review_service import ReviewWorkflowService

    service = ReviewWorkflowService(app_settings, logging.getLogger("test_d2_selection_ok"))
    service._title_candidates = lambda bundle: [*INVALID_TITLES, VALID_TITLES[0]]
    service._log_title_skeleton_collapse = lambda *a, **k: None

    bundle = type("Bundle", (), {"article": BODY, "titles": [], "recommended_titles": []})()
    service._load_bundle = lambda run_dir: (bundle, None)

    selection = service.select_auto_title(tmp_path)

    assert selection["selected_title"] == VALID_TITLES[0]
    assert selection["status"] == "partial"
    assert selection["passed_count"] == 1
