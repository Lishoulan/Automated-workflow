# Story supply and feed lead repair plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the opportunity for verified reading and income by repairing qualified source supply and duplicated story micro-post openings. Establish separate 7/30-day format and protocol baselines; never substitute publication counts for actual income.

**Architecture:** Retain the existing account, material, originality and publication gates. Add one small owner for court-source candidate discovery and short material-failure cooldowns, then merge and rank all eligible candidates before a shared bounded retrieval loop. Story writing uses a continuous factual first paragraph and a new opening protocol; old posts and batch evidence remain intact.

**Tech Stack:** Existing Python, urllib/HTMLParser, pytest, GitHub Actions; existing model and publishing services.

**Spec:** docs/superpowers/specs/2026-10-15-reader-value-rollout-design.md, plus the user's explicit 2026-10-17 request to solve insufficient qualified topics and exposure without reads.

## Global Constraints

- Keep the four practical story tracks; do not admit entertainment or machine demonstrations through incidental keywords.
- Keep the 900-character full-article / two-independent-source evidence gate, fact anchors, originality, public-policy and health-advice blocks.
- No new model calls per candidate: existing writer plus one factual editing pass, current models, fees, cloud independence and daily limits.
- Retain actual source text and explicit publication date; old cases are labeled as reviews, never fresh news or statements of current law.
- Retrieval at most 8 candidates per requested slot (hard cap 16), shared 180-second allowance, at most 25 seconds per candidate. Stop unused hydration once the caller has enough valid posts.
- A 6-hour failed-material cooldown is only an efficiency hint, never approval or publication state; malformed hints cannot bypass evidence checks.
- Keep the current immutable seven-work account batch. New opening comparisons use protocol 4 and never pool protocol 3 as current evidence.
- No editing/reposting the existing public micro-post, no extra automatic publication during acceptance, no database migration.
- Run the required focused read-only simplify-codebase audit; do not implement cleanup from it.
- Latest user constraint: historical artifacts, saved source HTML and Mock only. No platform-account actions, additional publications, paid model calls, workflow dispatch, promotion or deployment in this task. Preserve the dirty primary checkout and deliver the linked production-based worktree diff.

## Review Focus

1. Court listing/footer/navigation text cannot count toward article sufficiency; primary-container truncation must fail closed.
2. Source feeds and persisted candidates must reject external lookalike hosts, non-article links, unsafe URLs and unverified source dates.
3. Cooldowns, lookup failures and budget expiry must not reset publication state or hide a newly complete source.
4. A continuous lead must preserve qualifications and facts through export/sanitization; no unreviewed truncation or invented conflict.
5. Other account personas, old reports and statistical comparisons remain compatible; actual growth stays unknown until observed.

### Task 1: Repair source-first candidate supply

**Files:** create `src/toutiao_content_factory/services/story_source_service.py`; modify `weitoutiao_generator.py`, `automation.py`, `material_search_service.py`, `wechat_evidence.py`, `reader_value_service.py`; create `tests/test_story_source_supply.py`.

**Interfaces:** `StorySourceService(output_dir, logger).candidates(deadline=...)`, `.can_retry(topic)`, `.record_result(topic, passed)`. Sources enter the existing `_available_topics` and `_rank_topics` filters, and `_hydrate_topic_material(..., deadline=...)` remains the evidence authority.

- [ ] Reproduce the failed run: 17 fallback candidates cut to4, all4 weak while a later original report is usable; watch the regression fail.
- [ ] Add tests for same-host detail links, poisoned cache, source-only seeds, full article isolation and explicit header date; no body/footer date inference.
- [ ] Implement source link discovery from bounded public court listings and verified direct-link seeds; persist only source catalogue/cooldown hints under the already-cached material directory.
- [ ] Merge all candidates before ranking, apply 180/25-second budgets and propagate the deadline to non-WeChat material collection. Avoid repeat failed searches for6h; valid material is always rechecked.
- [ ] Add contextual consumer/marriage-service and disputed family-payment signals without admitting entertainment gossip.
- [ ] Run focused source, account-boundary, material, WeChat date and legacy generator tests; commit and ledger.

### Task 2: Use a continuous, specific feed opening

**Files:** modify `weitoutiao_generator.py` and `growth_feedback_service.py`; add `tests/test_story_feed_lead.py`; update the previous current-protocol regression in `tests/test_toutiao_read_conversion.py`.

**Interfaces:** story writer/reviewer retain text-completion interface and two calls. New `story_opening_v1` assignments carry protocol4; legacy reports remain readable.

- [ ] Add a regression for the actual disconnected headline plus repeated case sentence; require a factual two-sentence first paragraph, bounded in length and keeping the qualification.
- [ ] Write prompts and a final format check for protocol4; do not silently rewrite reviewed facts after the editing pass. Keep non-story output unchanged.
- [ ] Count the complete story body, including the lead, in source-copy checks for protocol4. Preserve separate legacy opening comparisons.
- [ ] Verify model-call counts, long/repeated lead rejection, unsupported facts, original meaning through package/export/publisher sanitization, and protocol isolation; commit and ledger.

### Task 3: Historical baseline, offline replay and final review

**Files:** extend `scripts/run_publication_stress.py`; retain offline regression fixtures. Store data-analysis and audit evidence outside the checkout under `E:/Agent/.runtime/toutiao-supply-lead-20261017/`.

- [ ] Read the requested README, growth/income modules and status report. Verify production SHA read-only; inventory local dirty changes without overwriting them.
- [ ] Build a reproducible 7/30-day baseline from captured platform records, separately for articles, micro-posts and incompatible experiment protocols. Mark missing attribution, settlement windows and costs unknown.
- [ ] Replay the actual failed topic pools, saved source HTML and latest zero-read lead; preserve body/date evidence and unchanged rejection thresholds. Historical cases must carry a review label and verified report year.
- [ ] Document an operational title/lead experiment with same-window 24/72-hour impressions, weighted reading rate, per-work reads and attributable revenue. No inference from undersized, late or incompatible samples.
- [ ] Run focused tests and full isolated publication regression; perform one fresh whole-branch review and one TDD fix pass for important findings.
- [ ] Perform the focused read-only simplification audit without implementing audit-driven cleanup.
- [ ] Deliver changed files, test results, evidence-backed bottlenecks and next action. No cloud dispatch, live model acceptance, platform-account access or actual growth claim.
