# Settled revenue feedback implementation

> Execute inline with executing-plans; use TDD and one final independent review.

**Goal:** Make Toutiao selection adaptive without pretending that missing or incompatible income proves a profitable niche.
**Architecture:** Extend the existing revenue dashboard and growth feedback. Keep source hydration, review, quotas, publishing and WeChat unchanged. No extra model calls or publication.
**Tech:** Python, existing JSON artifacts, pytest, offline platform artifact replay.
**Spec:** The user's 2026-10-17 request (seven tasks and execution principles); this plan records its implementable scope.

## Global Constraints

- Production main is 138ff10; reuse the clean linked worktree at 5111f32 and its existing source/lead repairs. Do not modify the dirty primary checkout or deploy.
- Unknown settlement, costs, category, competition or attribution remain unknown. Calendar income cannot be divided by creation-cohort lifetime traffic. Full windows require every day; pending/conflicting days cannot become zero.
- Adaptive selection is an account opt-in, disabled for WeChat. It changes editorial fit/ranking, not freshness, sources, independent-source checks, quality, compliance or permissions.
- Compatible learning partitions format, category, selection policy, opening protocol, variant and checkpoint. Late/invalid/unassigned data cannot tune production. Require 15 works and 1000 impressions per group; settled return needs aligned, exact per-content money, 1000 reads and multiple paying works. Adjust priority within evidence tiers; never stop a niche from unknown income.
- No extra paid calls, true account operations, workflow dispatch, push or deployment.

## Review Focus

Check persisted legacy JSON, future/invalid dates and amounts, cutoff mismatch, missing daily/cost history, sparse and mixed-protocol data, fake category evidence, first-use cold start, source quality, alternate candidate paths, exact submission binding, stale feedback and WeChat isolation.

### Task 1: Correct economic windows and denominator scope

**Files:** services/revenue_experiment_service.py, services/creator_account_service.py, related tests.
**Interfaces:** Existing dashboard keys remain readable; add 7/30/90-day coverage/observed vs complete income and creation-cohort metrics; keep actual cost/net unknown without receipts. Preserve real daily history in the existing account snapshot artifact rather than a second database.

1. Add failing tests for incomplete/pending windows, conflicts and 90-day coverage, calendar-income/lifetime-counter mismatch and missing/invalid exact income cutoff.
2. Run the tests; expect assertion failures against the old dashboard.
3. Implement minimal window diagnostics and bounded daily history in existing snapshot storage; no synthetic backfill.
4. Run revenue/account/cost tests and inspect results; expected all green.
5. Commit only Task 1 changes locally.

### Task 2: Adaptive selection and compatible content learning

**Files:** config.py/config.yaml, services/revenue_feedback.py, growth_feedback_service.py, automation.py, topic_collection.py, weitoutiao_generator.py, reader_value_service.py as needed, scripts/collect_growth_snapshot.py, related tests.
**Interfaces:** Optional account flag; existing feedback adds compatible groups and score diagnostics. Metadata and existing experiment ledger retain category and selection policy. Preserve opening protocol4 and isolate old/new selection cohorts.

1. Add failing pure feedback and pipeline tests: unknown/late/mixed samples cannot learn; sufficient compatible matched revenue can influence score; category outside old allowlist can reach source hydration; quality and WeChat behavior preserved; exact submitted-opening join retains metadata.
2. Run tests and watch expected failures.
3. Implement bounded source-first scoring, cold-start exploration, immutable group identity and metadata propagation. Reuse existing collection, writer, review and checkpoint storage. No extra generation call.
4. Run focused tests plus existing supply, lead, publishing/WeChat, growth and economics regressions. Replay latest and historical real artifacts offline; record 7/30/90 coverage and provenance. Expected no claimed winner or aligned RPR from present sparse history.
5. Commit Task 2 locally.

### Task 3: Verify and deliver

**Files:** Runtime evidence under E:/Agent/.runtime/toutiao-revenue-adaptive-20261017; no production edits from audits.

1. Run final related regression suite and bounded offline replay. Save evidence; do not call platform or models.
2. Dispatch exactly one fresh whole-change reviewer per executing-plans. Re-grade findings, one TDD fix pass for Important/Critical only; no re-review.
3. Run mandatory focused read-only simplify-codebase audit. Report only evidenced candidates with consumers/confidence/risk/smallest check. Do not implement cleanup.
4. Final report contains only the user's five requested sections: blockers, code+validation, niche-income evidence, 24/72h evaluation, next three actions. Clearly distinguish local changes from deployed production and actual income from hypotheses.
