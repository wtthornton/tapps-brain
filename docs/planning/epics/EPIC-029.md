---
id: EPIC-029
title: "Feedback collection — LLM and project quality signals"
status: done
completed: 2026-03-23
priority: high
created: 2026-03-23
tags: [feedback, quality, observability, flywheel]
---

# EPIC-029: Feedback Collection — LLM and Project Quality Signals

## Context

tapps-brain has strong observability (EPIC-007: metrics, audit trail, health checks) but no mechanism for consumers — LLMs, agents, projects, or humans — to report how well retrieval is working. The system can tell you *what* happened (audit log) and *how fast* (metrics), but not *whether it helped*.

Research (Airbnb AITL, NVIDIA data flywheel) shows that closing the feedback loop yields +11-15% retrieval improvements. The most effective systems collect both explicit signals (ratings, gap reports) and implicit signals (usage patterns, reformulations). Critically, <10% of users provide explicit feedback, so implicit collection must be automatic and zero-friction. GitHub Copilot research (CHI 2024, AAAI 2024) established that implicit signals like acceptance/rejection tracking and sentiment in developer prompts yield **13x more signal** than explicit thumbs-up/down.

tapps-brain already has one implicit positive signal — `reinforce()` — but no negative signals, no gap detection, and no structured feedback storage. This epic adds a feedback collection layer that captures quality signals from all three interfaces (library, CLI, MCP) and stores them in a queryable SQLite table alongside the existing memory database.

**Multi-project design**: Each `MemoryStore` instance gets its own `feedback_events` table (project-scoped by default). When Hive is enabled, feedback on Hive-sourced entries can propagate to the shared Hive feedback namespace (STORY-029.7). Host projects can register custom event types (STORY-029.8) to track application-level quality signals beyond memory retrieval.

## Success Criteria

- [x] `store.rate_recall()` accepts explicit quality ratings on recall results
- [x] `store.report_gap()` captures queries where the knowledge base lacks coverage
- [x] `store.report_issue()` flags specific entries as stale, wrong, duplicate, or harmful
- [x] Implicit signals (recall-not-reinforced, query reformulation, recall-then-store) are tracked automatically
- [x] All feedback stored in a dedicated `feedback_events` SQLite table (same DB, new table)
- [x] `store.query_feedback()` provides filtered access to feedback history
- [x] MCP tools exposed: `feedback_rate`, `feedback_gap`, `feedback_issue`, `feedback_record`, `feedback_query`
- [x] CLI commands: `tapps-brain feedback rate|gap|issue|record|list`
- [x] Host projects can register custom event types for application-level feedback
- [x] Feedback on Hive-sourced entries propagates to shared Hive namespace (when Hive enabled)
- [x] Zero new external dependencies
- [x] Overall test coverage stays at 95%+

## Stories

### STORY-029.1: Feedback data model and storage

**Status:** done
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/models.py`, `src/tapps_brain/persistence.py`
**Verification:** `pytest tests/unit/test_feedback.py -v --cov=tapps_brain.feedback --cov-report=term-missing`

#### Why

All feedback — explicit and implicit — needs a consistent data model and durable storage. A dedicated SQLite table in the existing database keeps everything co-located, queryable, and backed up together. Schema migration extends the existing v7 migration path. The event taxonomy follows Object-Action snake_case naming (Mixpanel/Amplitude 2025 consensus) with an open enum pattern for forward-compatible extensibility.

#### Acceptance Criteria

- [ ] New `src/tapps_brain/feedback.py` module with `FeedbackEvent` Pydantic model
- [ ] `FeedbackEvent` fields: `id` (UUID), `timestamp` (ISO-8601), `event_type` (str — validated against known types but accepts registered custom types), `query` (optional str), `entry_keys` (optional list[str]), `rating` (optional enum: `helpful`, `partial`, `irrelevant`, `outdated`), `issue_type` (optional enum: `stale`, `wrong`, `duplicate`, `harmful`), `description` (optional str), `session_id` (optional str), `source_project` (optional str — for Hive-propagated feedback), `metadata` (optional dict)
- [ ] Event type taxonomy uses **open enum** pattern: built-in types (`recall_rated`, `gap_reported`, `issue_flagged`, `implicit_positive`, `implicit_negative`, `implicit_reformulation`, `implicit_correction`) are validated; additional types accepted when registered via `FeedbackConfig.custom_event_types`
- [ ] Event naming follows Object-Action convention: `recall_rated` not `rating`, `gap_reported` not `gap`
- [ ] New `feedback_events` table in SQLite schema (migration v7 → v8)
- [ ] `FeedbackStore` class with `record(event: FeedbackEvent)` and `query(event_type=None, since=None, until=None, entry_key=None, limit=100) -> list[FeedbackEvent]`
- [ ] Thread-safe via `threading.Lock` (consistent with `MemoryStore` pattern)
- [ ] Audit log entry emitted for each feedback event
- [ ] Unit tests for model validation (including open enum behavior), storage round-trip, and query filtering

---

### STORY-029.2: Explicit feedback API

**Status:** done
**Effort:** M
**Depends on:** STORY-029.1
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/feedback.py`
**Verification:** `pytest tests/unit/test_feedback.py::TestExplicitFeedback -v`

#### Why

LLMs and projects need a clean API to report retrieval quality. Three distinct actions cover the feedback taxonomy: rate a recall result, report a knowledge gap, and flag a problematic entry. These are the explicit signals that feed into the improvement flywheel (EPIC-031).

#### Acceptance Criteria

- [ ] `store.rate_recall(query: str, entry_keys: list[str], rating: str, session_id: str | None = None)` — records a `recall_rated` event; validates rating is one of `helpful`, `partial`, `irrelevant`, `outdated`
- [ ] `store.report_gap(query: str, description: str, session_id: str | None = None)` — records a `gap_reported` event for queries where the knowledge base has no relevant content
- [ ] `store.report_issue(entry_key: str, issue_type: str, description: str | None = None)` — records an `issue_flagged` event; validates issue_type is one of `stale`, `wrong`, `duplicate`, `harmful`; validates entry_key exists
- [ ] `store.record_feedback(event_type: str, **kwargs)` — generic method for recording any event type (built-in or custom); validates event_type is known or registered
- [ ] `store.query_feedback(**kwargs)` — delegates to `FeedbackStore.query()`
- [ ] All methods emit audit log entries
- [ ] Unit tests for each method including validation errors and custom event type recording

---

### STORY-029.3: Implicit feedback tracking

**Status:** done
**Effort:** L
**Depends on:** STORY-029.1
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/recall.py`, `src/tapps_brain/feedback.py`
**Verification:** `pytest tests/unit/test_feedback.py::TestImplicitFeedback -v`

#### Why

Explicit feedback covers <10% of interactions. Research from GitHub Copilot (CUPS taxonomy, CHI 2024) and the CDHF framework (AAAI 2024) shows that implicit signals — acceptance/rejection, reformulation, post-acceptance editing — provide **13x more signal volume** than explicit feedback. The CDHF utility model (`utility = f(acceptance_probability, latency)`) provides a principled framework for valuing implicit signals. For tapps-brain, implicit signals must be collected lazily (no background threads) with zero friction.

#### Acceptance Criteria

- [ ] **Recall-then-reinforce tracking**: when `reinforce()` is called for a key that was returned in a recent `recall()` (same session or within configurable window, default 5 min), record an `implicit_positive` event linking the query to the reinforced key
- [ ] **Recall-not-reinforced tracking**: when a recall result is not followed by `reinforce()` for any returned key within the window, record an `implicit_negative` event (deferred/batch — checked on next recall or session boundary, not via background thread). Note: following Copilot research, this is a *weak* negative signal — absence of reinforcement is ambiguous (satisfaction or irrelevance)
- [ ] **Query reformulation detection**: when two `recall()` calls occur within 60s in the same session with similar queries (Jaccard similarity > 0.5 on tokenized terms), record an `implicit_reformulation` event linking both queries — this is the strongest implicit negative signal (user is actively trying to get better results)
- [ ] **Recall-then-store correction**: when `save()` is called within the tracking window and the new entry's value has high token overlap (>40%) with a recently recalled entry, record an `implicit_correction` event linking the old and new keys
- [ ] **Utility scoring**: each implicit event records a `utility_score` in metadata: positive events score `1.0`, reformulations score `-0.5`, corrections score `-0.3`, not-reinforced scores `-0.1` (weak negative). These scores feed into EPIC-031's Bayesian confidence updating
- [ ] Session tracking via optional `session_id` parameter on `recall()` and `save()` (backward-compatible — `None` disables implicit tracking)
- [ ] Configurable tracking window via `FeedbackConfig` (defaults: reinforce_window=300s, reformulation_window=60s, correction_overlap_threshold=0.4)
- [ ] No background threads — all detection is lazy (evaluated on subsequent calls)
- [ ] Unit tests for each implicit signal type with timing/overlap edge cases and utility score verification

---

### STORY-029.4: MCP feedback tools

**Status:** done
**Effort:** M
**Depends on:** STORY-029.2
**Context refs:** `src/tapps_brain/mcp_server.py`, `src/tapps_brain/feedback.py`
**Verification:** `pytest tests/unit/test_mcp_server.py::TestFeedbackTools -v`

#### Why

MCP is a first-class interface. LLMs interacting via MCP need tools to provide feedback without breaking out to the library API. Tools mirror the library API plus a generic recording tool for custom event types. Note: the MCP spec (2025-11) does not yet define a standardized quality feedback mechanism — these tools fill that gap using custom tool definitions.

#### Acceptance Criteria

- [ ] `feedback_rate` tool: accepts `query`, `entry_keys`, `rating`; returns confirmation with event ID
- [ ] `feedback_gap` tool: accepts `query`, `description`; returns confirmation with event ID
- [ ] `feedback_issue` tool: accepts `entry_key`, `issue_type`, `description`; returns confirmation with event ID
- [ ] `feedback_record` tool: generic — accepts `event_type`, optional `query`, `entry_keys`, `description`, `metadata`; returns confirmation. Enables host projects to use custom event types via MCP
- [ ] `feedback_query` tool: accepts optional `event_type`, `since`, `until`, `entry_key`, `limit`; returns list of feedback events as JSON
- [ ] `memory://feedback` resource: returns summary stats (event counts by type, recent events)
- [ ] Tool descriptions are clear enough for an LLM to understand when to use them
- [ ] Unit tests for each tool including error cases

---

### STORY-029.5: CLI feedback commands

**Status:** done
**Effort:** S
**Depends on:** STORY-029.2
**Context refs:** `src/tapps_brain/cli.py`, `src/tapps_brain/feedback.py`
**Verification:** `pytest tests/unit/test_cli.py::TestFeedbackCommands -v`

#### Why

CLI is a first-class interface. Human operators and scripts need to submit and query feedback from the terminal.

#### Acceptance Criteria

- [ ] `tapps-brain feedback rate <query> --keys <key1,key2> --rating <rating>` — submits a rating
- [ ] `tapps-brain feedback gap <query> --description <text>` — reports a knowledge gap
- [ ] `tapps-brain feedback issue <entry_key> --type <issue_type> [--description <text>]` — flags an entry
- [ ] `tapps-brain feedback record <event_type> [--query <q>] [--keys <k>] [--description <d>]` — generic recording
- [ ] `tapps-brain feedback list [--type <event_type>] [--since <date>] [--limit <n>]` — queries feedback
- [ ] Output format: JSON (default) or table (--format table)
- [ ] Unit tests for each command with exit codes

---

### STORY-029.6: Integration tests

**Status:** done
**Effort:** M
**Depends on:** STORY-029.2, STORY-029.3, STORY-029.4
**Context refs:** `tests/integration/`
**Verification:** `pytest tests/integration/test_feedback_integration.py -v`

#### Why

Validates the full feedback pipeline: explicit + implicit collection, storage, query, and MCP/CLI exposure working together against a real SQLite store.

#### Acceptance Criteria

- [ ] Integration test: perform recalls, submit ratings, verify feedback events stored and queryable
- [ ] Integration test: perform recall → reinforce sequence, verify implicit_positive event created with utility_score=1.0
- [ ] Integration test: perform recall → no reinforce → next recall, verify implicit_negative event created with utility_score=-0.1
- [ ] Integration test: perform two similar recalls within 60s, verify reformulation event created with utility_score=-0.5
- [ ] Integration test: perform recall → save overlapping entry, verify correction event created
- [ ] Integration test: register custom event type, record event, verify stored and queryable
- [ ] Integration test: verify feedback events appear in audit trail
- [ ] All tests use real `MemoryStore` + SQLite (no mocks)

---

### STORY-029.7: Federated feedback propagation

**Status:** done
**Effort:** M
**Depends on:** STORY-029.2, EPIC-011 (Hive)
**Context refs:** `src/tapps_brain/hive.py`, `src/tapps_brain/feedback.py`
**Verification:** `pytest tests/unit/test_feedback.py::TestFederatedFeedback -v`

#### Why

When feedback targets a Hive-sourced memory, it should propagate to the Hive so other consumers benefit. Without this, five projects could independently discover the same bad Hive entry and none of them fix it. Research (DQFed, FedAWA 2025) shows that quality-weighted aggregation — where agents with historically better signal quality get higher influence — outperforms naive aggregation. The Hive's existing namespace isolation and agent identity tracking provide natural boundaries.

#### Acceptance Criteria

- [ ] `HiveStore` gains a `hive_feedback_events` table (mirrors local schema, adds `source_project_id`)
- [ ] When `rate_recall()` or `report_issue()` targets an entry that was sourced from Hive (detectable via entry metadata or hive recall flag), the feedback event is recorded locally AND propagated to `hive_feedback_events` with `source_project_id` set to current project
- [ ] Feedback on private/local-only entries is NOT propagated (respects `agent_scope`)
- [ ] Hive feedback is append-only — no conflict resolution needed (all perspectives are valuable)
- [ ] `hive_store.query_feedback(entry_key=..., namespace=...)` queries aggregated feedback across all projects for a given Hive entry
- [ ] Propagation is synchronous (same transaction as local record) but failure-tolerant (local record succeeds even if Hive write fails; failure logged to audit)
- [ ] Backward-compatible: disabled when Hive is not enabled (zero code path change for non-Hive users)
- [ ] Unit tests for propagation, non-propagation of local entries, and Hive write failure tolerance

---

### STORY-029.8: Custom feedback event types

**Status:** done
**Effort:** S
**Depends on:** STORY-029.1
**Context refs:** `src/tapps_brain/feedback.py`, `src/tapps_brain/_protocols.py`
**Verification:** `pytest tests/unit/test_feedback.py::TestCustomEventTypes -v`

#### Why

Host projects embedding tapps-brain (like TheStudio) need to track application-level quality signals beyond memory retrieval — `user_satisfaction`, `task_completed`, `response_quality`, `feature_used`. The open enum pattern (recommended by Confluent schema evolution and OTel event naming research) provides forward-compatible extensibility without forking the feedback infrastructure.

#### Acceptance Criteria

- [ ] `FeedbackConfig.custom_event_types: list[str]` — additional valid event types beyond built-in set
- [ ] Custom event type names validated against Object-Action snake_case convention: `[a-z][a-z0-9]*(_[a-z][a-z0-9]*)+` pattern
- [ ] `store.record_feedback(event_type="task_completed", ...)` works for registered custom types; raises `ValueError` for unregistered types
- [ ] Custom events stored in the same `feedback_events` table with the same schema (no separate storage)
- [ ] Custom events participate in `query_feedback()` filtering and `memory://feedback` resource counts
- [ ] Built-in implicit tracking only fires for built-in event types (custom types are always explicit)
- [ ] Profile-level configuration: custom event types can be defined in project profile YAML under `feedback.custom_event_types`
- [ ] Unit tests for registration, validation (reject bad names), recording, and querying of custom types

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-029.1 — Feedback data model and storage | M | Foundation: schema, model, storage |
| 2 | STORY-029.8 — Custom event types | S | Extends 029.1; enables host project use early |
| 3 | STORY-029.2 — Explicit feedback API | M | Core library API (blocks MCP + CLI) |
| 4 | STORY-029.3 — Implicit feedback tracking | L | Highest value signal source; parallel with 029.4/029.5 |
| 5 | STORY-029.4 — MCP feedback tools | M | Can parallel with 029.3 after 029.2 |
| 6 | STORY-029.5 — CLI feedback commands | S | Can parallel with 029.3 after 029.2 |
| 7 | STORY-029.7 — Federated feedback propagation | M | Requires Hive; independent of 029.3-029.5 |
| 8 | STORY-029.6 — Integration tests | M | Final validation |

## Dependency Graph

```
029.1 (model/storage) ──┬──→ 029.8 (custom types)
                        │
                        └──→ 029.2 (explicit API) ──┬──→ 029.3 (implicit tracking) ──┐
                                                     │                                │
                                                     ├──→ 029.4 (MCP tools) ──────────┤
                                                     │                                │
                                                     ├──→ 029.5 (CLI commands) ───────┤
                                                     │                                │
                                                     └──→ 029.7 (Hive propagation) ───┼──→ 029.6 (integration)
```

029.3, 029.4, 029.5, and 029.7 can be worked in parallel after 029.2 is complete.
