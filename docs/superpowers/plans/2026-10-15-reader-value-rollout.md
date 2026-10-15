# Reader Value Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 将已授权的题材聚焦、正文信息价值和5—7篇真实阅读验收接入云端工作流。

**Architecture:** 编辑规则与效果观察分别拥有独立模块。现有生成/事实审核仍负责事实与发布准入，现有performance目录保存短批次状态；现有采集和日报展示验收，不增加模型调用。

**Tech Stack:** Python、pytest、现有GitHub Actions。

**Spec:** `docs/superpowers/specs/2026-10-15-reader-value-rollout-design.md`

## Global Constraints

- 公众号仅存草稿；每日90分钟及现有模型、次数和费用配置不变。
- 24小时允许延迟12小时，72小时允许延迟24小时；窗口分开、迟到排除。
- 第一阶段2%；阅读中位数须增加，展现中位数不得下降。
- 5篇提醒、7篇复盘；旧记录可读，账号隔离，不自动重开批次或推断因果。
- 不额外实发或调用收费模型作验证；完成聚焦只读simplify-codebase审计和一次独立分支评审。

## Review Focus

- 娱乐或科技话题夹带“女儿/老人”等词不能绕过头条题材聚焦；素材完整门槛保持。
- 只有热榜标题、背景旧文或原文中指令时，不能变成已核验的读者答案。
- 摘要提取不得增加正文没有的事实、截断否定或破坏审核对象绑定。
- 重复采集、并发写入、账号或策略不匹配、陈旧快照不得重置基线或制造新样本。
- 仅阅读率升高、展现下降，或迟到/零分母样本，不能生成改善结论。

### Task 1: 聚焦选题与来源简报

**Files:** Create `src/toutiao_content_factory/services/reader_value_service.py`; modify `weitoutiao_generator.py`, `app_service.py`; test `tests/test_reader_value_rollout.py`.

**Interfaces:** `story_reader_fit(topic) -> dict`, `build_reader_brief(title: str, quotes: list[str], *, profile: str) -> dict`, `wechat_value_priority(topic, facts: list[dict]) -> int`.

- [ ] Write failing tests: story pool excludes celebrity checkup and tourist technology despite incidental keywords; property/labor/consumer works remain; non-story account unchanged. Source-ready topics precede weaker material. WeChat useful product facts precede pure market-cap ranking only when factual sufficiency is comparable. Brief quotes must be verbatim distinct source sentences.
- [ ] Run `python -m pytest -q -o addopts= tests/test_reader_value_rollout.py`; verify expected missing behavior fails.
- [ ] Implement pure editorial rules and connect actual selection paths; persist selection diagnostics without changing factual scores or budgets.
- [ ] Run task tests and `tests/test_topic_selection_evidence.py tests/test_weitoutiao_generator.py tests/test_wechat_daily_evidence_first.py` until green, then commit this task.
- [ ] Completion check: same combined test command passes, ledger records the result.

### Task 2: 正文与摘要信息价值

**Files:** Modify `reader_value_service.py`, `weitoutiao_generator.py`, `agents/writer_agent.py`; tests in `test_reader_value_rollout.py`, existing WeChat publication tests.

**Interfaces:** `generic_padding_reason(text: str) -> str`; `grounded_summary(summary: str, article: str) -> str`. Consumes Task 1 brief, no new model call.

- [ ] Write failing tests: known generic padding cannot be published; concrete sourced explanation remains; WeChat abstract or unsupported summary falls back to complete body sentences without new facts or cutting negation; blank or oversized first sentence preserves an honest empty summary when no safe extract exists.
- [ ] Run these cases and observe failures before implementing.
- [ ] Add source-specific brief to the existing writing/editing requests, remove forced repetitive transition, enforce known-padding check on final text and ground WeChat summaries before audit; retain original fact and safety gates.
- [ ] Run `python -m pytest -q -o addopts= tests/test_reader_value_rollout.py tests/test_toutiao_read_conversion.py tests/test_weitoutiao_generator.py tests/test_wechat_publication_loop.py tests/test_wechat_daily_evidence_first.py`; commit after green.
- [ ] Completion check: same command passes, ledger records the result.

### Task 3: 5—7篇短批次验收与云端上线

**Files:** Create `src/toutiao_content_factory/services/reader_value_experiment.py`; modify `growth_feedback_service.py`, `revenue_experiment_service.py`, `scripts/collect_growth_snapshot.py`, `weitoutiao_generator.py`, `scripts/run_publication_stress.py`; test `tests/test_reader_value_experiment.py`.

**Interfaces:** `ensure_batch(output_dir: Path, *, account: str) -> dict`; `update_batch(output_dir: Path, snapshot: dict, checkpoints: dict, *, account: str) -> dict`. Baseline and cohort stored at `growth_ops/performance/reader_value_batch.json`, summary returned for daily reports.

- [ ] Write failing tests: baseline immutable and account-scoped; no seed if snapshot missing; at most7 new real works; edits and repeats excluded; old/malformed/mismatched state not reset; stale observations do not mutate state; windows independent, late captures excluded; 5 prompts review and7 produces only descriptive verdict; ratio improvement with exposure decline cannot pass; missing baseline/data stays unknown.
- [ ] Run `python -m pytest -q -o addopts= tests/test_reader_value_experiment.py` and observe expected failures.
- [ ] Implement atomic state writes under existing cloud run lock and read-only-stat aggregation; seed before generation, update after real snapshot; add batch report to feedback/dashboard, align first-stage goal to2%, replace wait-only action text. Include new tests in isolated publication stress runner.
- [ ] Run focused tests, save replay against real27-work snapshot and known bad/good content; run the isolated publication suite, commit.
- [ ] Run a fresh whole-branch review, address important findings with RED→GREEN, perform focused read-only simplification audit. Validate Linux scale3 in GitHub Actions and promote exact tested tree using the existing bounded helper pattern.
- [ ] Completion check: remote main equals tested commit; receipt states actual checks and no paid-model or publication side effects. Preserve user-facing evidence and report actual limitations.
