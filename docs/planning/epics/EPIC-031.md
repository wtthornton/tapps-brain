---
id: EPIC-031
title: "Continuous improvement flywheel — feedback-driven quality loop"
status: planned
priority: medium
created: 2026-03-23
tags: [flywheel, continuous-improvement, evaluation, self-healing]
---

# EPIC-031: Continuous Improvement Flywheel — Feedback-Driven Quality Loop

## Context

EPIC-029 collects feedback signals. EPIC-030 assesses quality and detects anomalies. This epic closes the loop: it turns feedback and diagnostics into concrete actions that improve retrieval quality over time — automatically where safe, with human approval where not.

The data flywheel pattern (NVIDIA, Airbnb AITL) is well-established: collect signals, analyze patterns, generate improvements, apply them, measure impact. Airbnb reported +11.7% recall and +14.8% precision from this loop. The key design constraint for tapps-brain is that the flywheel must be **fully deterministic** — no LLM calls in the core loop. Optional LLM-as-judge evaluation is available for CI/offline use but never required at runtime.

Research on confidence calibration (Glicko-2, Beta-Binomial Bayesian updating, Wilson score intervals) provides principled methods for translating qualitative feedback into numeric confidence adjustments — significantly more robust than simple multiplicative penalties. Gap detection research (SDEC, HDBSCAN with sentence embeddings) enables semantic clustering of knowledge gaps beyond simple token overlap. The offline evaluation ecosystem (BEIR, ir-measures, ARES) provides mature formats and metrics for deterministic retrieval testing.

The flywheel has three components:
1. **Feedback → Action pipeline**: Bayesian confidence updates from feedback, knowledge gap tracking with semantic clustering, and scoring recommendations
2. **Offline evaluation harness**: BEIR-format golden datasets with deterministic IR metrics (Precision@K, Recall@K, MRR, NDCG)
3. **Self-report generation**: Protocol-based extensible reports summarizing quality trends and recommended actions

**Multi-project design**: The flywheel runs per-store (project-scoped). Cross-project feedback aggregation (STORY-031.8) uses the Hive feedback namespace to identify ecosystem-wide patterns. Report templates (STORY-031.9) are extensible so host projects can add custom sections.

## Success Criteria

- [ ] Feedback signals influence confidence scores via Bayesian updating (principled, not ad-hoc)
- [ ] Knowledge gaps from `report_gap()` are tracked, clustered, and prioritized
- [ ] Offline evaluation harness runs deterministic retrieval quality tests against BEIR-format golden datasets
- [ ] Self-reports summarize quality trends, top gaps, and recommended actions via extensible sections
- [ ] Cross-project feedback aggregation identifies ecosystem-wide patterns via Hive
- [ ] All flywheel operations are deterministic — no LLM calls required
- [ ] Optional LLM-as-judge evaluator available for CI (behind feature flag)
- [ ] MCP tools and CLI commands for evaluation and reports
- [ ] Zero new required dependencies (LLM-as-judge requires optional `anthropic` or `openai` SDK)
- [ ] Overall test coverage stays at 95%+

## Stories

### STORY-031.1: Feedback-to-confidence pipeline

**Status:** planned
**Effort:** L
**Depends on:** EPIC-029 (STORY-029.1, STORY-029.2)
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/feedback.py`, `src/tapps_brain/decay.py`
**Verification:** `pytest tests/unit/test_flywheel.py::TestConfidencePipeline -v`

#### Why

Feedback signals should directly influence entry confidence — the single most impactful lever in tapps-brain's retrieval scoring (30% weight). Research on rating systems (Glicko-2, Bayesian Elo, Wilson score) shows that **Beta-Binomial Bayesian updating** provides principled confidence adjustment that handles sparse feedback correctly, quantifies uncertainty, and avoids the instability of simple multiplicative penalties. Glicko-style volatility dampening per tier ensures that architectural knowledge (180d half-life) changes slowly while context knowledge (14d half-life) responds quickly — matching tapps-brain's existing decay philosophy.

#### Acceptance Criteria

- [ ] New `src/tapps_brain/flywheel.py` module with `FeedbackProcessor` class
- [ ] **Beta-Binomial Bayesian updating** for confidence:
  - Each entry maintains `positive_feedback_count` and `negative_feedback_count` (new fields, default 0)
  - Jeffreys prior: `Beta(positive + 0.5, negative + 0.5)` — handles the zero-feedback case gracefully
  - Feedback signal mapping: `helpful` and `implicit_positive` → positive count + 1; `irrelevant`, `outdated` → negative count + 1; `partial` → no change (neutral); `implicit_negative` → negative count + 0.2 (weak signal); `implicit_correction` → old entry negative + 0.5
  - New confidence = `(positive + 0.5) / (positive + negative + 1.0)` (Beta mean with Jeffreys prior)
- [ ] **Glicko-style volatility dampening** per tier:
  - `K_factor = base_K * tier_volatility[entry.tier]` where `tier_volatility = {architectural: 0.3, pattern: 0.5, procedural: 0.7, context: 1.0}`
  - `confidence_delta = K_factor * (bayesian_confidence - entry.confidence)`
  - `entry.confidence = entry.confidence + confidence_delta`
  - This ensures architectural knowledge changes slowly (K=0.3) while context changes quickly (K=1.0)
- [ ] Minimum confidence floor: `0.05` (configurable — never fully zero out an entry)
- [ ] `process_feedback(store, since=None)` scans recent feedback events and applies updates
- [ ] De-duplication: each feedback event processed exactly once (track last-processed event ID)
- [ ] Configurable via `FlywheelConfig`: `base_K` (default 1.0), `tier_volatility` overrides, `min_confidence` floor
- [ ] Audit log entry for each confidence adjustment with source feedback event ID, old confidence, new confidence
- [ ] `store.process_feedback()` convenience method
- [ ] Unit tests for Bayesian math, tier dampening, de-duplication, floor enforcement, and edge cases (first feedback on fresh entry, many feedbacks converging)

---

### STORY-031.2: Knowledge gap tracker

**Status:** planned
**Effort:** M
**Depends on:** EPIC-029 (STORY-029.1)
**Context refs:** `src/tapps_brain/flywheel.py`, `src/tapps_brain/feedback.py`
**Verification:** `pytest tests/unit/test_flywheel.py::TestKnowledgeGaps -v`

#### Why

`report_gap()` events identify queries where the knowledge base has no relevant content. Research (InfraNodus, SDEC 2025) shows that gap detection must go beyond frequency counting — semantic clustering identifies that "how to deploy" and "deployment process" are the same gap, and prioritization by `frequency x impact x trend` surfaces the most actionable gaps. For tapps-brain, the default clustering uses Jaccard token similarity (zero dependencies), with optional upgrade to sentence embeddings + HDBSCAN when the vector extras are installed.

#### Acceptance Criteria

- [ ] `GapTracker` class in `flywheel.py`
- [ ] `analyze_gaps(store, since=None) -> list[KnowledgeGap]` aggregates `gap_reported` feedback events
- [ ] `KnowledgeGap` model: `query_pattern` (representative query or cluster centroid), `count` (number of gap reports), `first_reported` (timestamp), `last_reported` (timestamp), `descriptions` (list of user descriptions, deduplicated), `priority_score` (float)
- [ ] **Default clustering** (zero dependencies): Jaccard token similarity > 0.6 groups queries into a single gap. Representative query = the shortest query in the cluster (most general)
- [ ] **Optional semantic clustering** (when `HAS_SENTENCE_TRANSFORMERS` flag is True): embed gap queries with sentence transformer, cluster with HDBSCAN (min_cluster_size=3, metric=cosine). Noise points treated as potential novel/unique gaps, not discarded
- [ ] **Gap prioritization**: `priority_score = count * tier_weight * trend_factor` where:
  - `tier_weight` = estimated impact based on related entry tiers (architectural gaps > context gaps; default 1.0 if no related entries)
  - `trend_factor` = 1.5 if gap frequency is increasing over last 30 days, 1.0 if stable, 0.7 if decreasing
- [ ] `top_gaps(limit=10) -> list[KnowledgeGap]` returns gaps sorted by `priority_score` descending
- [ ] **Zero-result query tracking**: `recall()` calls that return 0 results are automatically logged as weak gap signals (no explicit `report_gap()` needed). These contribute `count += 0.5` to gap clustering
- [ ] Gaps surfaced in `DiagnosticsReport.recommendations` (EPIC-030 integration): "N knowledge gaps reported. Top gap: '<query_pattern>' (priority: X, reported M times)"
- [ ] `store.knowledge_gaps(limit=10)` convenience method
- [ ] Unit tests for gap aggregation, both clustering algorithms, prioritization scoring, and zero-result tracking

---

### STORY-031.3: Offline evaluation harness

**Status:** planned
**Effort:** L
**Depends on:** none
**Context refs:** `src/tapps_brain/retrieval.py`, `src/tapps_brain/store.py`
**Verification:** `pytest tests/unit/test_evaluation.py -v --cov=tapps_brain.evaluation --cov-report=term-missing`

#### Why

Retrieval quality must be measurable and regression-testable. The BEIR benchmark (NeurIPS 2021) established the standard format for IR evaluation: corpus.jsonl + queries.jsonl + qrels.tsv. Adopting this format ensures compatibility with the broader IR evaluation ecosystem. All metrics (Precision@K, Recall@K, MRR, NDCG@K) are pure-Python, deterministic, and require no external dependencies. Microsoft research recommends **150-300 golden test cases** for statistical significance at 95% confidence with 5% margin of error.

#### Acceptance Criteria

- [ ] New `src/tapps_brain/evaluation.py` module
- [ ] **BEIR-compatible data format** support:
  - `EvalCorpus`: loads from JSONL (`{"_id": "key", "title": "...", "text": "..."}`) — maps to tapps-brain entries
  - `EvalQueries`: loads from JSONL (`{"_id": "q1", "text": "..."}`)
  - `EvalQrels`: loads from TSV (`query_id\tcorpus_id\tscore`) — relevance grades 0-3
  - `EvalSuite`: wraps corpus + queries + qrels with metadata; also serializable to/from YAML for human-editable suites
- [ ] `evaluate(store, suite: EvalSuite, k=5) -> EvalReport` runs all queries against a live store
- [ ] `EvalReport` model: `suite_name`, `timestamp`, per-query results, aggregate metrics, pass/fail verdict
- [ ] **Pure-Python metrics** (no external dependencies):
  - **Precision@K**: fraction of top-K results that are relevant (grade > 0)
  - **Recall@K**: fraction of relevant documents that appear in top-K results
  - **MRR** (Mean Reciprocal Rank): mean of 1/rank of first relevant result across queries
  - **NDCG@K** (Normalized Discounted Cumulative Gain): `DCG@K / idealDCG@K` using relevance grades as gain, `log2(rank + 1)` as discount
- [ ] `EvalReport` includes pass/fail based on configurable thresholds (default: MRR >= 0.5, NDCG@5 >= 0.5)
- [ ] Sample golden dataset at `tests/eval/` in BEIR format (10-20 cases for unit tests; production suites should target 150+ cases)
- [ ] Unit tests for each metric calculation with known inputs/outputs (verified against published BEIR results)
- [ ] Unit test: load sample dataset, evaluate against pre-populated store, verify metrics

---

### STORY-031.4: Optional LLM-as-judge evaluator

**Status:** planned
**Effort:** M
**Depends on:** STORY-031.3
**Context refs:** `src/tapps_brain/evaluation.py`, `src/tapps_brain/_feature_flags.py`
**Verification:** `pytest tests/unit/test_evaluation.py::TestLLMJudge -v`

#### Why

Golden datasets require manual curation. For teams that want automated relevance assessment, an LLM can judge whether retrieved memories are relevant to a query. Research (G-Eval, RULERS 2026) shows that **pointwise binary scoring** is the most reliable LLM-as-judge method — LLMs struggle with fine-grained scales but achieve ~85-90% agreement with humans on binary relevant/irrelevant. The **cascaded judging** pattern (Trust or Escalate, ICLR 2025) reduces costs by 80%+ by using a cheap model first and escalating only uncertain cases.

#### Acceptance Criteria

- [ ] `LLMJudge` Protocol in `evaluation.py`: `judge_relevance(query: str, memory_value: str) -> JudgeResult`
- [ ] `JudgeResult` model: `score` (float 0.0-1.0), `reasoning` (str — chain-of-thought), `confident` (bool — whether judge is confident in the score)
- [ ] Default implementation: `AnthropicJudge` using Claude API (optional `anthropic` dependency)
- [ ] Alternative: `OpenAIJudge` stub (optional `openai` dependency)
- [ ] Feature flags: `HAS_ANTHROPIC` / `HAS_OPENAI` in `_feature_flags.py`, lazy detection
- [ ] **Pointwise binary scoring** as default: prompt asks "Is this memory relevant to the query? Score 0 (irrelevant) or 1 (relevant)" with chain-of-thought reasoning. Optional 0-3 scale mode for finer-grained assessment
- [ ] **Structured output**: prompt requests JSON `{"reasoning": "...", "score": 0|1, "confident": true|false}`
- [ ] `evaluate_with_judge(store, queries: list[str], judge: LLMJudge, k=5) -> EvalReport` — auto-generates relevance grades from judge scores, then computes standard IR metrics
- [ ] **Cascaded judging** support: `CascadedJudge` wraps a cheap judge and an expensive judge. Uses cheap judge first; escalates to expensive judge when `confident=False`. Tracks escalation rate
- [ ] When no LLM SDK is installed, judge raises `FeatureNotAvailable` on instantiation
- [ ] Unit tests with mocked LLM responses verifying score parsing, cascading logic, and metric computation
- [ ] Unit test: verify graceful `FeatureNotAvailable` when SDKs not installed

---

### STORY-031.5: Self-report generation

**Status:** planned
**Effort:** M
**Depends on:** STORY-031.1, STORY-031.2, EPIC-030 (STORY-030.3)
**Context refs:** `src/tapps_brain/flywheel.py`, `src/tapps_brain/diagnostics.py`, `src/tapps_brain/_protocols.py`
**Verification:** `pytest tests/unit/test_flywheel.py::TestSelfReport -v`

#### Why

Periodic self-reports summarize quality trends, feedback patterns, knowledge gaps, and recommended actions into a human-readable document. Research (Allure 3 plugin architecture, Spring Actuator, SRE postmortem templates) shows that the most effective reporting systems use **Protocol-based extensible sections** — each section decides whether to include itself and renders independently. This enables host projects to add custom sections without modifying core code. Report rendering uses Python string formatting (not Jinja2 — zero new dependencies).

#### Acceptance Criteria

- [ ] `ReportSection` Protocol in `_protocols.py`: `name: str`, `priority: int` (lower = earlier in report), `should_include(data: ReportData) -> bool`, `render(data: ReportData) -> str`
- [ ] `ReportData` model: `store` reference, `diagnostics_history`, `feedback_summary`, `knowledge_gaps`, `eval_results` (optional), `custom_data` (dict — host projects inject their own data)
- [ ] `generate_report(store, period_days=7, extra_sections=None, custom_data=None) -> QualityReport` in `flywheel.py`
- [ ] `QualityReport` model:
  - `period_start`, `period_end` (timestamps)
  - `sections` (list of rendered section outputs)
  - `rendered_text` (full markdown report — concatenated sections)
  - `structured_data` (JSON-serializable summary for programmatic access)
- [ ] **Built-in sections** (each implements `ReportSection`):
  1. **Health Summary** (priority 10): composite score trend, current grade, circuit breaker state
  2. **Dimension Breakdown** (priority 20): per-dimension scores with trend arrows (improving/stable/declining)
  3. **Anomaly Alerts** (priority 30): active anomalies with timestamps and recommendations
  4. **Feedback Summary** (priority 40): counts by event type, rating distribution, top low-rated entries
  5. **Knowledge Gaps** (priority 50): top 5 gaps with priority scores
  6. **Recommendations** (priority 100): prioritized action list
- [ ] `rendered_text` is a concise markdown document (target: 20-40 lines) following SRE postmortem summary structure: Summary → Impact → Details → Actions
- [ ] Optional: store report as a tapps-brain meta-memory entry (tier=context, source=system, tagged `self-report`) — configurable, default=False
- [ ] `store.generate_report(period_days=7)` convenience method
- [ ] Unit tests with synthetic data verifying section inclusion/exclusion, ordering, and markdown rendering

---

### STORY-031.6: MCP and CLI exposure

**Status:** planned
**Effort:** M
**Depends on:** STORY-031.3, STORY-031.5
**Context refs:** `src/tapps_brain/mcp_server.py`, `src/tapps_brain/cli.py`
**Verification:** `pytest tests/unit/test_mcp_server.py::TestFlywheelTools tests/unit/test_cli.py::TestFlywheelCommands -v`

#### Why

The flywheel's outputs — evaluation results, knowledge gaps, quality reports — must be accessible from all interfaces. MCP tools let LLMs trigger evaluations and read reports. CLI lets operators integrate into scripts and dashboards.

#### Acceptance Criteria

- [ ] `flywheel_process` MCP tool: triggers `process_feedback()`, returns count of adjustments made and summary
- [ ] `flywheel_gaps` MCP tool: returns top knowledge gaps as JSON with priority scores
- [ ] `flywheel_report` MCP tool: generates and returns quality report (rendered_text + structured data)
- [ ] `flywheel_evaluate` MCP tool: runs evaluation suite against store, returns EvalReport (requires suite file path)
- [ ] `memory://report` MCP resource: returns latest quality report summary
- [ ] `tapps-brain flywheel process` CLI command: runs feedback-to-confidence pipeline
- [ ] `tapps-brain flywheel gaps [--limit <n>]` CLI command: lists knowledge gaps with priority scores
- [ ] `tapps-brain flywheel report [--period <days>] [--format json|markdown]` CLI command: generates report
- [ ] `tapps-brain flywheel evaluate <suite_path> [--k <n>] [--format json|table]` CLI command: runs evaluation (supports BEIR-format directory or YAML suite file)
- [ ] Unit tests for MCP tool responses and CLI output

---

### STORY-031.7: Integration tests

**Status:** planned
**Effort:** L
**Depends on:** STORY-031.1, STORY-031.3, STORY-031.5
**Context refs:** `tests/integration/`
**Verification:** `pytest tests/integration/test_flywheel_integration.py -v`

#### Why

Validates the full flywheel loop: feedback → Bayesian confidence adjustment → improved diagnostics score → report. Tests the end-to-end data flow against real storage.

#### Acceptance Criteria

- [ ] Integration test: store entries, submit negative feedback, run `process_feedback()`, verify confidence decreased via Bayesian update (not just multiplicative penalty)
- [ ] Integration test: store entries, submit positive feedback, run `process_feedback()`, verify confidence increased proportionally to tier volatility
- [ ] Integration test: submit multiple gap reports with similar queries, run `analyze_gaps()`, verify clustering and priority scoring
- [ ] Integration test: populate store with golden dataset entries, run evaluation, verify metrics match expected values
- [ ] Integration test: run full flywheel cycle (store → recall → feedback → process → diagnostics → report), verify report reflects feedback
- [ ] Integration test: verify feedback processing is idempotent (running twice doesn't double-apply updates)
- [ ] Integration test: verify Bayesian confidence converges over many feedback signals (simulate 50+ feedbacks, verify convergence to expected range)
- [ ] All tests use real `MemoryStore` + SQLite (no mocks)

---

### STORY-031.8: Cross-project feedback aggregation

**Status:** planned
**Effort:** M
**Depends on:** STORY-031.1, EPIC-029 (STORY-029.7)
**Context refs:** `src/tapps_brain/flywheel.py`, `src/tapps_brain/hive.py`
**Verification:** `pytest tests/unit/test_flywheel.py::TestCrossProjectAggregation -v`

#### Why

Feedback on Hive-shared entries is siloed per project (STORY-029.7 propagates to Hive, but no one aggregates). Cross-project aggregation identifies ecosystem-wide patterns: an entry consistently rated `irrelevant` across 4 projects is a stronger signal than in 1 project. Research on federated analytics (Mayfly, FAItH 2025) shows that aggregating bounded summaries — not raw data — preserves privacy while enabling fleet-wide quality monitoring.

#### Acceptance Criteria

- [ ] `aggregate_hive_feedback(hive_store, since=None) -> HiveFeedbackReport` scans Hive feedback namespace
- [ ] `HiveFeedbackReport` model:
  - `entry_feedback` (dict: entry_key → aggregated rating distribution across projects, project count)
  - `cross_project_gaps` (list of KnowledgeGap clustered across all projects' gap reports in Hive)
  - `issue_hotspots` (entries with issues flagged by 2+ projects)
  - `total_projects_reporting` (int)
- [ ] Entry-level feedback aggregation: entry with negative feedback from N+ projects (configurable threshold, default 3) gets flagged for Hive-level confidence penalty
- [ ] `process_hive_feedback(hive_store, threshold=3)` applies confidence penalties to Hive entries based on aggregated cross-project feedback
- [ ] Cross-project gap clustering: gap reports from different projects grouped by query similarity (using same clustering as STORY-031.2)
- [ ] Exposed via `flywheel_hive_feedback` MCP tool and `tapps-brain flywheel hive-feedback` CLI command
- [ ] Backward-compatible: no-op when Hive is not enabled
- [ ] Unit tests for aggregation, threshold enforcement, and cross-project gap clustering

---

### STORY-031.9: Extensible report templates

**Status:** planned
**Effort:** S
**Depends on:** STORY-031.5
**Context refs:** `src/tapps_brain/flywheel.py`, `src/tapps_brain/_protocols.py`
**Verification:** `pytest tests/unit/test_flywheel.py::TestReportTemplates -v`

#### Why

Host projects need to add custom report sections without modifying core code. TheStudio might want sections on "User Satisfaction Trends" or "Task Completion Rates" alongside tapps-brain's built-in health sections. The `ReportSection` Protocol from STORY-031.5 provides the interface; this story adds the registration mechanism and demonstrates host project extensibility.

#### Acceptance Criteria

- [ ] `ReportRegistry` class: maintains ordered list of `ReportSection` implementations
- [ ] `register(section: ReportSection)` adds a section; `unregister(name: str)` removes one
- [ ] Default registry pre-populated with built-in sections from STORY-031.5
- [ ] `generate_report()` accepts optional `registry: ReportRegistry` parameter (uses default if not provided)
- [ ] Host projects can register custom sections at store init time via `FlywheelConfig.custom_report_sections`
- [ ] Custom sections receive `ReportData.custom_data` dict (host projects inject their own data via `generate_report(custom_data={"satisfaction_scores": [...]})`)
- [ ] Priority-based ordering: sections render in priority order; host project sections can interleave with built-in sections
- [ ] Example in tests: a `UserSatisfactionSection` that renders a custom metric from `custom_data`
- [ ] Unit tests for registration, ordering, custom section rendering, and interaction with built-in sections

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-031.3 — Offline evaluation harness | L | Independent; highest standalone value for CI |
| 2 | STORY-031.1 — Feedback-to-confidence pipeline | L | Core flywheel action; depends on EPIC-029 |
| 3 | STORY-031.2 — Knowledge gap tracker | M | Depends on EPIC-029; feeds into reports |
| 4 | STORY-031.5 — Self-report generation | M | Depends on 031.1 + 031.2 + EPIC-030 |
| 5 | STORY-031.9 — Extensible report templates | S | Quick win after 031.5; enables host projects |
| 6 | STORY-031.4 — LLM-as-judge evaluator | M | Optional; depends on 031.3 |
| 7 | STORY-031.8 — Cross-project aggregation | M | Requires Hive + EPIC-029 federated feedback |
| 8 | STORY-031.6 — MCP and CLI exposure | M | Depends on 031.3 + 031.5 |
| 9 | STORY-031.7 — Integration tests | L | Final validation |

## Dependency Graph

```
EPIC-029 ──┬──→ 031.1 (Bayesian confidence) ──┬──→ 031.5 (reports) ──→ 031.9 (templates) ──┐
           │                                   │                                              │
           └──→ 031.2 (gap tracker) ───────────┘                                              │
                                                                                              │
EPIC-029.7 ──→ 031.8 (cross-project aggregation)                                             │
                                                                                              ├──→ 031.6 (MCP + CLI) ──→ 031.7
031.3 (eval harness) ──┬──→ 031.4 (LLM judge)                                                │
                       │                                                                      │
                       └──────────────────────────────────────────────────────────────────────→ ┘

EPIC-030 (diagnostics history) ──→ 031.5 (reports)
```

031.3 is fully independent and can start immediately. 031.1 and 031.2 require EPIC-029. 031.5 requires 031.1 + 031.2 + EPIC-030. 031.8 requires Hive + EPIC-029 federated feedback.
