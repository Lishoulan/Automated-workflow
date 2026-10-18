# robot_notes Controlled Profit Trial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. One focused independent review at the end; no additional implementation agents.

**Goal:** Put two reader-value directions into the existing production slots, bind real work IDs to generation costs and valid checkpoint/income evidence, and make weekly/day30 decisions without inventing profit.

**Architecture:** Extend the existing account-specific revenue policy, submission ledger, cost receipts and revenue report. The current scheduled production run has already yielded work1879355709157440; preserve that record and all prior evidence. No new agents, dashboards, queues or publication frequency.

**Tech Stack:** Existing Python dataclasses/JSON, pytest isolation guard, GitHub Actions unchanged.

**Spec:** `E:/Agent/.runtime/robot-notes-30d-20261018/operating-spec.md`, based on the user's seven operating requirements.

## Global Constraints

- robot_notes only; WeChat and other account behavior remain compatible.
- Existing facts, source age/independence, originality, quality/compliance and publication authorization remain mandatory.
- Enrollment2026-10-18T00:00+08:00 to2026-11-17T00:00+08:00; no retroactive experiment assignment.
- Existing schedule, quotas, model configuration and90-minute daily processing budget; no extra paid/model/publication acceptance calls.
-24h only at24–36h,72h only at72–96h; no mixed format/protocol/body-structure/trial groups.
- Actual fees and net contribution remain unknown without matched provider receipts and complete relevant income/cost coverage.
- Keep old primary modifications and the previous signed acceptance/activation files untouched.

## Review Focus

- A cheaper or missing-evidence direction cannot become factually approved through balancing.
- Identical titles, multiple candidates in a run, rejected candidates and worker-thread image generation cannot share an inferred cost allocation.
- Later metadata cannot relabel captured checkpoints or historical works.
- Unknown charges, stale/mismatched income and missing monetary rows cannot create a positive-margin decision.
- Trial defaults, other accounts, expiry and existing Cloudflare fallback preserve compatibility and no extra calls.

### Task 1: Two-direction selection, reader-value and attempt receipts

**Files:** config.py/config.yaml; revenue_feedback.py; generation_cost_service.py; weitoutiao_generator.py; image_generation_service.py; targeted tests.

**Interfaces:** account revenue_optimization.operating_trial is optional JSON. Pure policy returns a validated trial ID/start/end and canonical directions. Receipt context carries immutable content_attempt_id/account/topic/trial metadata. Legacy defaults remain empty.

- [ ] Write failing tests for only-two-direction admission, evidence-first ordering within direction and bounded alternate fallback, trial/protocol separation, missing/bad state, no history relabeling, cost account/thread isolation, and direct-free image routing.
- [ ] Run those tests under the existing external-transport guard and confirm failures expose missing behavior.
- [ ] Add the minimal account-specific policy and call-site metadata; keep hard publication gates and current limits.
- [ ] Strengthen the opening/analysis instructions against the observed announcement-first/generic-advice draft without editing existing posts.
- [ ] Bind text and image calls to attempt IDs; preserve failed attempts and unknown charges.
- [ ] Run focused new and directly affected existing regressions; record outcomes and commit this task.

### Task 2: Existing report becomes a per-work operating ledger and decision record

**Files:** collect_growth_snapshot.py; revenue_experiment_service.py; necessary review metadata call-site; targeted tests.

**Interfaces:** optional content_lineage and trial/body-structure experiment fields travel only through existing unique identity joins. The existing revenue report contains per-work operating records and weekly/day30 status; existing collection/report entrypoints own execution.

- [ ] Write failing tests for exact ID/cost linkage, duplicate/ambiguous joins, immutable capture identity, early/late checkpoints, actual-as-of income, unknown costs, rejected/unallocated fees and weekly insufficient-sample/day30 decisions.
- [ ] Run the tests red; implement the existing-ledger/report extensions without a new scheduled task.
- [ ] Reuse compatible checkpoint grouping to recommend weak opening reductions only after reading/exposure gates; otherwise keep balanced exploration.
- [ ] Run focused regression and replay archived data (including the first new production work) offline, with no account or model calls.
- [ ] Commit, obtain one independent whole-change review and a focused read-only simplify-codebase audit; fix evidence-backed blockers only.
- [ ] Prepare an exact production-base candidate and concrete activation evidence. Obtain any new production authorization required by the existing boundary; do not manually dispatch content.
- [ ] Record actual first-workT0, next existing24/72h collection opportunities and30-day closing questions. Leave future outcomes explicitly pending.
