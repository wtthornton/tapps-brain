# Ralph Fix Plan — tapps-brain

All tasks complete as of **2026-03-23**. For full story text, see `docs/planning/epics/EPIC-*.md`.

**Task sizing:** Each item is scoped to ONE Ralph loop (~15 min). Do one, check it off, commit.

## Completed Epics

- EPIC-001 through EPIC-016 (core features, test hardening)
- BUG-001: Pre-review critical fixes (7 bugs)
- BUG-002: Source trust regression & uncommitted WIP (6 tasks)
- EPIC-017: Code review — Storage & Data Model (8 tasks)
- EPIC-018: Code review — Retrieval & Scoring (5 tasks)
- EPIC-019: Code review — Memory Lifecycle (5 tasks)
- EPIC-020: Code review — Safety & Validation (5 tasks)
- EPIC-021: Code review — Federation, Hive & Relations (4 tasks)
- EPIC-022: Code review — Interfaces (MCP, CLI, IO) (7 tasks)
- EPIC-023: Code review — Config, Profiles & Observability (3 tasks)
- EPIC-024: Code review — Unit Tests (14 tasks)
- EPIC-025: Code review — Integration Tests, Benchmarks & TypeScript (7 tasks)
- EPIC-026: OpenClaw Memory Replacement (6 tasks)
- EPIC-027: OpenClaw Full Feature Surface — All 41 MCP Tools (9 tasks)
- EPIC-028: OpenClaw Plugin Hardening (9 tasks)

Full history archived in `fix_plan.md.bak-20260323`.

## Next Tasks

---

### EPIC-033: OpenClaw Plugin SDK Alignment (GitHub #4, #5, #6, #7)

**Priority: CRITICAL — plugin crashes and broken functionality**

- [x] **033-1** Import SDK types: add `openclaw` dev dep, replace custom `OpenClawPluginApi` interface with `import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core"`, remove `PluginEntryDef` if SDK provides equivalent. Fix all resulting TS compilation errors. (STORY-033.1, closes #6)
- [x] **033-2** Fix version detection: change `getCompatibilityMode()` to receive `api.runtime.version` instead of `api.version`. Verify log output shows correct OpenClaw version and full ContextEngine mode activates on >= 2026.3.7. (STORY-033.2, closes #4)
- [x] **033-3** Fix workspace/session resolution: replace `api.runtime.workspaceDir` with `api.runtime.agent.resolveAgentWorkspaceDir()`, fix `sessionId` from correct `PluginRuntime` property. Add defensive fallback to `process.cwd()`. (STORY-033.3, closes #5)
- [x] **033-4** Fix migration script: read `config.plugins.entries[OLD_NAME]` and `config.plugins.installs[OLD_NAME]`, write to `entries[NEW_NAME]`/`installs[NEW_NAME]`, backward-compatible fallback for older config formats. (STORY-033.4, closes #7)
- [x] **033-QA** Run `npm run build && npm test` for openclaw-plugin. Verify all 4 issues are resolved.

---

### EPIC-029: Feedback Collection — LLM and Project Quality Signals

**Priority: HIGH — foundational for EPIC-030/031 flywheel**

- [x] **029-1a** Feedback data model: create `src/tapps_brain/feedback.py` with `FeedbackEvent` Pydantic model (open enum for `event_type` — validated str, not closed Literal), `FeedbackStore` class, `feedback_events` SQLite table (migration v7→v8). Event naming follows Object-Action snake_case: `recall_rated`, `gap_reported`, `issue_flagged`, `implicit_positive`, etc. (STORY-029.1)
- [x] **029-1b** Feedback storage and query: implement `FeedbackStore.record()` and `.query()` with filtering (event_type, since, until, entry_key, limit), thread-safe via Lock, audit log emission. Unit tests for model validation (including open enum behavior), round-trip, and query filtering. (STORY-029.1)
- [x] **029-2** Custom event types: add `FeedbackConfig.custom_event_types`, validate Object-Action snake_case naming pattern (`[a-z][a-z0-9]*(_[a-z][a-z0-9]*)+`), integrate with `FeedbackStore.record()` and `query()`, profile YAML support. Unit tests. (STORY-029.8)
- [x] **029-3** Explicit feedback API: add `store.rate_recall()`, `store.report_gap()`, `store.report_issue()`, `store.record_feedback()` (generic for custom types), `store.query_feedback()` to MemoryStore. Audit log integration. Unit tests. (STORY-029.2)
- [ ] **029-4a** Implicit feedback — positive/negative signals: implement recall-then-reinforce tracking (implicit_positive with utility_score=1.0) and recall-not-reinforced tracking (implicit_negative, utility_score=-0.1, weak signal per Copilot CDHF research). Session_id parameter on recall()/save(). Configurable FeedbackConfig window (default 300s). Unit tests. (STORY-029.3 part 1)
- [ ] **029-4b** Implicit feedback — reformulation/correction: implement query reformulation detection (Jaccard similarity > 0.5 within 60s, utility_score=-0.5) and recall-then-store correction tracking (token overlap > 40%, utility_score=-0.3). No background threads — all lazy. Unit tests for timing/overlap edge cases. (STORY-029.3 part 2)
- [ ] **029-5** MCP feedback tools: add `feedback_rate`, `feedback_gap`, `feedback_issue`, `feedback_record` (generic for custom event types), `feedback_query` tools and `memory://feedback` resource to mcp_server.py. Unit tests. (STORY-029.4)
- [ ] **029-6** CLI feedback commands: add `tapps-brain feedback rate|gap|issue|record|list` subcommands with JSON/table output. Unit tests with exit codes. (STORY-029.5)
- [ ] **029-7** Federated feedback propagation: add `hive_feedback_events` table to HiveStore, propagate feedback on Hive-sourced entries (detect via entry metadata), failure-tolerant sync (local succeeds even if Hive write fails). No propagation for private/local-only entries. Unit tests. (STORY-029.7)
- [ ] **029-8** Integration tests: full feedback pipeline — explicit + implicit collection with utility scores, storage, query, custom event types, MCP/CLI exposure, Hive propagation, audit trail. Real SQLite, no mocks. (STORY-029.6)
- [ ] **029-QA** Run full test suite: `pytest tests/ -v --tb=short --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95`. Lint + type check.

---

### EPIC-030: Diagnostics & Self-Monitoring — Quality Scorecard and Anomaly Detection

**Priority: HIGH — quality observability layer**

- [ ] **030-1a** Quality scoring foundation: create `src/tapps_brain/diagnostics.py`, add `HealthDimension` Protocol to `_protocols.py` (`name`, `check(store) -> DimensionScore`, `default_weight`). Define `DimensionScore`/`DiagnosticsReport` Pydantic models. Composite scoring with configurable weights (auto-normalized to sum=1.0). Goalpost normalization (0.0-1.0 per dimension). (STORY-030.1)
- [ ] **030-1b** Built-in dimensions: implement six `HealthDimension` instances — retrieval_effectiveness (hit rate + mean score), freshness (age vs tier half-life), completeness (field population), duplication (consolidation candidate ratio), staleness (stale-but-served ratio), integrity (HMAC verification). Unit tests for each with edge cases (empty store, full store, all stale). (STORY-030.1)
- [ ] **030-1c** Correlation down-weighting: when 20+ history snapshots exist, compute Pearson correlation between dimension score series; reduce combined weight of pairs with r > 0.7 by 30% (redistributed to uncorrelated dimensions). Unit tests. (STORY-030.1)
- [ ] **030-2** Quality history: add `diagnostics_history` SQLite table (migration v8→v9 or bundled with 029), `DiagnosticsStore.record()`/`.history()`/`.rolling_average()`, auto-recording on `store.diagnostics()`, retention pruning (90d default). Unit tests. (STORY-030.3)
- [ ] **030-3** Custom quality dimensions: `DiagnosticsConfig.custom_dimensions` accepts list of `HealthDimension` Protocol implementations, weight normalization includes custom dims, profile YAML support via importable dotted paths. Example `ResponseLatencyDimension` in tests. Unit tests. (STORY-030.8)
- [ ] **030-4** Anomaly detection (EWMA): `AnomalyDetector` class using Exponentially Weighted Moving Average (lambda=0.2, self-start baseline after 20 observations). Dual-threshold: 2σ → `threshold_warning`, 3σ → `threshold_critical`. Confirmation window of 3 consecutive signals before alerting. EWMA state recomputed from history on startup. Dimension-specific recommendations. Unit tests with degradation sequences. (STORY-030.2)
- [ ] **030-5a** Circuit breaker — state machine: `CircuitBreaker` with 4 states (CLOSED ≥0.6, DEGRADED 0.3-0.6, OPEN <0.3, HALF_OPEN recovery). `RecallResult.quality_warning` field (None when CLOSED). DEGRADED state: results + warning, optional disable of vector search/Hive. State transitions logged to audit trail. Unit tests for transitions. (STORY-030.4)
- [ ] **030-5b** Circuit breaker — auto-remediation and recovery: Tier 1 auto-remediation in OPEN state (consolidate when duplication <0.5, gc when staleness <0.5, integrity alert when <0.8). Per-action cooldown (1h). HALF_OPEN: graduated re-enablement with 3 probe diagnostics, jitter on cooldown timeout. Unit tests for cooldown, probes, recovery/failure. (STORY-030.4)
- [ ] **030-6** Feedback-aware scoring: upgrade retrieval effectiveness dimension to incorporate EPIC-029 feedback ratings when `feedback_events` table exists (helpful=1.0, partial=0.5, irrelevant=0.0, outdated=0.0 weighted at 0.4). Backward-compatible fallback. Gap count in recommendations. Unit tests for both paths. (STORY-030.5)
- [ ] **030-7** Per-namespace Hive diagnostics: `DiagnosticsReport.hive_diagnostics` dict (per-namespace DimensionScores for freshness/duplication/feedback). Aggregate `hive_composite_score` using worst-of semantics. Circuit breaker dynamically reduces `hive.recall_weight` (0.8→0.4 in DEGRADED, →0.0 in OPEN). Zero cost when Hive disabled. Unit tests. (STORY-030.9)
- [ ] **030-8** MCP and CLI exposure: `diagnostics_report`/`diagnostics_history` MCP tools, `memory://diagnostics` resource (includes circuit breaker state), `tapps-brain diagnostics` CLI with json/table formats (color-coded grades, Operational/Degraded/Partial Outage/Major Outage status labels). Unit tests. (STORY-030.6)
- [ ] **030-9** Integration tests: full diagnostics pipeline — scoring with real data, EWMA anomaly detection across multiple runs with confirmation window, 4-state circuit breaker transitions (CLOSED→DEGRADED→OPEN→HALF_OPEN→CLOSED), history persistence, auto-remediation with cooldown. Real SQLite. (STORY-030.7)
- [ ] **030-QA** Run full test suite with coverage gate. Lint + type check.

---

### EPIC-031: Continuous Improvement Flywheel — Feedback-Driven Quality Loop

**Priority: MEDIUM — closes the feedback→quality loop (depends on EPIC-029 + EPIC-030)**

- [ ] **031-1a** Eval harness — models and format: create `src/tapps_brain/evaluation.py` with `EvalCorpus`/`EvalQueries`/`EvalQrels` (BEIR-compatible: corpus.jsonl + queries.jsonl + qrels.tsv), `EvalSuite` (also YAML-serializable), `EvalReport` model with pass/fail thresholds. Unit tests for format loading. (STORY-031.3)
- [ ] **031-1b** Eval harness — metrics: pure-Python implementations of Precision@K, Recall@K, MRR, NDCG@K (DCG with log2 discount, ideal DCG normalization, 0-3 relevance grades). Unit tests with known inputs/outputs verified against published results. (STORY-031.3)
- [ ] **031-1c** Eval harness — evaluate function: `evaluate(store, suite, k=5) -> EvalReport` that runs all queries, computes per-query + aggregate metrics, determines pass/fail. Sample golden dataset at `tests/eval/` (10-20 cases). Integration test against pre-populated store. (STORY-031.3)
- [ ] **031-2a** Bayesian confidence pipeline — core math: create `src/tapps_brain/flywheel.py` with `FeedbackProcessor`. Beta-Binomial updating: each entry tracks `positive_feedback_count`/`negative_feedback_count`, Jeffreys prior Beta(+0.5, +0.5). Signal mapping: helpful/implicit_positive → +1, irrelevant/outdated → neg+1, partial → no change, implicit_negative → neg+0.2, implicit_correction → old entry neg+0.5. Unit tests for Bayesian math. (STORY-031.1)
- [ ] **031-2b** Bayesian confidence pipeline — tier dampening: Glicko-style volatility dampening: `K_factor = base_K * tier_volatility[tier]` (architectural=0.3, pattern=0.5, procedural=0.7, context=1.0). `confidence_delta = K * (bayesian - current)`. Min floor 0.05. De-duplication via last-processed event ID. Audit logging. `store.process_feedback()` convenience. Unit tests for dampening, floor, dedup, convergence over 50+ signals. (STORY-031.1)
- [ ] **031-3a** Knowledge gap tracker — core: `GapTracker` class, `KnowledgeGap` model (query_pattern, count, first/last_reported, descriptions, priority_score). Default Jaccard clustering (similarity > 0.6). Zero-result query auto-tracking (recall returning 0 results → weak gap signal, count += 0.5). `store.knowledge_gaps()` convenience. Unit tests. (STORY-031.2)
- [ ] **031-3b** Knowledge gap tracker — prioritization and optional HDBSCAN: gap priority = `count × tier_weight × trend_factor` (trend_factor: 1.5 increasing, 1.0 stable, 0.7 decreasing). Optional HDBSCAN + sentence embeddings clustering when `HAS_SENTENCE_TRANSFORMERS` (min_cluster_size=3, cosine distance, noise points preserved as novel gaps). Diagnostics integration ("N gaps reported. Top: ..."). Unit tests. (STORY-031.2)
- [ ] **031-4a** Self-report — Protocol and scaffold: `ReportSection` Protocol in `_protocols.py` (name, priority, should_include, render). `ReportData` model (store ref, diagnostics_history, feedback_summary, knowledge_gaps, eval_results, custom_data dict). `generate_report()` function, `QualityReport` model with rendered_text (markdown). Unit tests for scaffold. (STORY-031.5)
- [ ] **031-4b** Self-report — built-in sections: implement 6 sections as ReportSection: Health Summary (priority 10), Dimension Breakdown (20), Anomaly Alerts (30), Feedback Summary (40), Knowledge Gaps (50), Recommendations (100). SRE postmortem structure: Summary → Impact → Details → Actions. Optional meta-memory storage. `store.generate_report()` convenience. Unit tests with synthetic data. (STORY-031.5)
- [ ] **031-5a** LLM-as-judge — Protocol and binary scoring: `LLMJudge` Protocol (`judge_relevance(query, memory_value) -> JudgeResult`). `JudgeResult` model (score, reasoning, confident bool). Pointwise binary scoring prompt ("Is this memory relevant? 0 or 1") with chain-of-thought, structured JSON output. Feature flags `HAS_ANTHROPIC`/`HAS_OPENAI`. `FeatureNotAvailable` error. Unit tests with mocks. (STORY-031.4)
- [ ] **031-5b** LLM-as-judge — backends and cascading: `AnthropicJudge` and `OpenAIJudge` implementations. `CascadedJudge` wrapping cheap + expensive judge (escalates when confident=False, tracks escalation rate). `evaluate_with_judge()` auto-generates qrels from judge scores, computes standard IR metrics. Unit tests for cascading logic. (STORY-031.4)
- [ ] **031-6** Extensible report templates: `ReportRegistry` class (register/unregister sections, priority ordering). Default registry pre-populated with built-in sections. `generate_report(registry=...)` parameter. `FlywheelConfig.custom_report_sections` for host projects. Example `UserSatisfactionSection` in tests using `custom_data`. Unit tests. (STORY-031.9)
- [ ] **031-7** Cross-project feedback aggregation: `aggregate_hive_feedback(hive_store)` → `HiveFeedbackReport` (entry-level aggregated ratings, cross-project gap clusters, issue hotspots). `process_hive_feedback()` applies Hive confidence penalties when N+ projects (default 3) report negative feedback. MCP tool `flywheel_hive_feedback`, CLI `tapps-brain flywheel hive-feedback`. No-op without Hive. Unit tests. (STORY-031.8)
- [ ] **031-8** MCP and CLI exposure: `flywheel_process`/`flywheel_gaps`/`flywheel_report`/`flywheel_evaluate` MCP tools, `memory://report` resource. `tapps-brain flywheel process|gaps|report|evaluate` CLI (BEIR-format directory or YAML suite for evaluate). Unit tests. (STORY-031.6)
- [ ] **031-9** Integration tests: full flywheel cycle — Bayesian confidence updating (verify convergence, not just delta), gap clustering with priority scoring, golden dataset eval metrics, full loop (store→recall→feedback→process→diagnostics→report), idempotency. Real SQLite. (STORY-031.7)
- [ ] **031-QA** Run full test suite with coverage gate. Lint + type check.

---

### EPIC-032: OTel GenAI Semantic Conventions — Standardized Telemetry Export

**Priority: LOW — optional observability upgrade**

- [ ] **032-1a** GenAI + MCP span instrumentation: upgrade `otel_exporter.py` to create spans per MCP tool call. Span name: `{mcp.method.name} {gen_ai.tool.name}`. Attributes: `mcp.method.name` ("tools/call"), `mcp.session.id`, `gen_ai.operation.name` (recall→"retrieval", others→"execute_tool"), `gen_ai.data_source.id` ("tapps_brain"), `gen_ai.tool.name`. Span kind=SERVER. W3C Trace Context extraction from `params._meta.traceparent`. `OTelConfig` with enabled/service_name. Null-object pattern when HAS_OTEL=False. (STORY-032.1)
- [ ] **032-1b** Retrieval document events: within recall/search spans, emit structured retrieval document events per returned memory following official JSON schema: `{"id": "<key>", "score": <composite_score>}` with additional properties for tier and staleness. Unit tests with mocked OTel SDK verifying span structure, attributes, events match spec. (STORY-032.1)
- [ ] **032-2** GenAI + MCP metric conventions: map `recall_ms`/`save_ms`/`search_ms` → `gen_ai.client.operation.duration` (Histogram, unit=s). `mcp.server.operation.duration` (Histogram, MCP-spec buckets). `gen_ai.client.token.usage` for recall tokens. Custom `tapps_brain.*` prefix: `tapps_brain.entry.count` (UpDownCounter by tier), `.consolidation.candidates` (Gauge), `.gc.candidates` (Gauge), `.feedback.count` (Counter by event_type), `.diagnostics.composite_score` (Gauge), `.diagnostics.circuit_breaker_state` (Gauge 0-3). Cardinality: only low-cardinality attributes on metrics. Unit tests. (STORY-032.2)
- [ ] **032-3** Privacy controls: respect `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` env var (NO_CONTENT default, SPAN_ONLY, EVENT_ONLY, SPAN_AND_EVENT). tapps-brain-specific: `TAPPS_BRAIN_OTEL_ENABLED`, `TAPPS_BRAIN_OTEL_LOG_ENTRY_KEYS`, `TAPPS_BRAIN_OTEL_SERVICE_NAME`. Layered defense: content omission at application level before OTel pipeline. Disabled attributes omitted entirely (not redacted). Unit tests per privacy mode. (STORY-032.4)
- [ ] **032-4** Feedback and diagnostics events: emit OTel Events (LogRecords with `event.name`) using `tapps_brain.*` namespace: `tapps_brain.feedback.recall_rated`, `.feedback.gap_reported`, `.feedback.issue_flagged`, `tapps_brain.diagnostics.anomaly_detected`, `.diagnostics.circuit_breaker_transition`. Graceful skip via feature detection when EPIC-029/030 unavailable. Unit tests. (STORY-032.3)
- [ ] **032-5** Integration tests: MCP tool → convention-compliant OTel span pipeline (verify `gen_ai.*` + `mcp.*` attributes), metric export with correct names/types/units, privacy modes (NO_CONTENT vs SPAN_AND_EVENT), zero-overhead when HAS_OTEL=False, feedback/diagnostics events when available. Real store + InMemorySpanExporter. (STORY-032.5)
- [ ] **032-QA** Run full test suite with coverage gate. Lint + type check.
