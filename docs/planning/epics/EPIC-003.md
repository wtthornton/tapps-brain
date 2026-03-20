---
id: EPIC-003
title: "Auto-recall — pre-prompt memory injection hook"
status: done
priority: critical
created: 2026-03-19
target_date: 2026-04-15
completed: 2026-03-19
tags: [auto-recall, injection, retrieval, integration]
---

# EPIC-003: Auto-Recall — Pre-Prompt Memory Injection Hook

## Context

tapps-brain has a complete retrieval engine (BM25 + FTS5 + composite scoring + optional vector search + RRF fusion) and even an `inject_memories()` helper in `injection.py`. But there is **no orchestration layer** that automatically searches the store and injects relevant memories before an agent processes a user message.

Today, the agent must explicitly call `store.search()` or `inject_memories()` — if it doesn't decide to search, it hallucinates or repeats questions. This is the #1 pain point identified across the 2026 AI memory ecosystem:

- Mem0 reports +26% accuracy and ~90% token savings with automatic recall vs. manual search
- 10+ competing OpenClaw memory plugins exist, all solving this exact problem
- The consensus is that auto-recall is what separates a "storage system" from a "memory system"

tapps-brain's `injection.py` already handles formatting, safety checks, token budgets, and engagement levels. What's missing is:

1. A **recall orchestrator** that accepts an incoming message, searches the store, and returns injection-ready context
2. A **protocol/hook interface** so host agents (Claude Code, OpenClaw, custom) can plug in auto-recall without coupling to tapps-brain internals
3. **Quality gates** — deduplication against already-in-context memories, relevance thresholds, and staleness filtering
4. **Capture pipeline** — extract and persist new facts from agent responses back into the store

## Success Criteria

- [x] A `RecallOrchestrator` class exists that takes a user message and returns injection-ready context
- [x] Host agents can integrate auto-recall via a simple Protocol interface (< 10 lines of glue code)
- [x] Recall respects scope, tier, and branch filters
- [x] Token budget is enforced (default 2000 tokens, configurable)
- [x] New facts can be captured from agent responses and persisted to the store
- [x] Quality gates prevent duplicate, stale, or low-confidence memories from being injected
- [x] End-to-end integration test: message → recall → inject → respond → capture round-trip
- [x] Overall coverage stays at 95%+

## Stories

### STORY-003.1: Define the RecallHook protocol and RecallResult model

**Status:** done
**Effort:** S
**Depends on:** none
**Context refs:** `src/tapps_brain/_protocols.py`, `src/tapps_brain/models.py`, `src/tapps_brain/injection.py`
**Verification:** `pytest tests/unit/test_recall.py -v --cov=tapps_brain.recall --cov-report=term-missing`

#### Why

Host agents (Claude Code, OpenClaw, custom scripts) need a stable interface to integrate auto-recall without coupling to tapps-brain internals. A Protocol-based design lets any host implement the hook contract while tapps-brain provides the default implementation.

#### Acceptance Criteria

- [x] `RecallHookLike` Protocol defined in `_protocols.py` with method `recall(message: str, **kwargs) -> RecallResult`
- [x] `RecallResult` model in `models.py` with fields: `memory_section` (str), `memories` (list of injection summaries), `token_count` (int), `recall_time_ms` (float)
- [x] `CaptureHookLike` Protocol with method `capture(response: str, **kwargs) -> list[str]` (returns keys of captured memories)
- [x] Both protocols are runtime-checkable (`@runtime_checkable`)
- [x] Unit tests verify protocol structural subtyping works with a minimal stub implementation

---

### STORY-003.2: Implement the RecallOrchestrator

**Status:** done
**Effort:** L
**Depends on:** STORY-003.1
**Context refs:** `src/tapps_brain/injection.py`, `src/tapps_brain/retrieval.py`, `src/tapps_brain/store.py`, `src/tapps_brain/safety.py`
**Verification:** `pytest tests/unit/test_recall.py -v --cov=tapps_brain.recall --cov-report=term-missing`

#### Why

This is the core component — the orchestrator that ties retrieval, scoring, safety, and formatting into a single `recall()` call. It wraps `inject_memories()` with additional quality gates and makes auto-recall a first-class operation.

#### Acceptance Criteria

- [x] `RecallOrchestrator` class in new `src/tapps_brain/recall.py` module
- [x] Constructor accepts: `store`, `retriever` (optional, defaults to `MemoryRetriever()`), `config` (optional `RecallConfig`)
- [x] `RecallConfig` dataclass with fields: `engagement_level` (low/medium/high, default "high"), `max_tokens` (int, default 2000), `min_score` (float, default 0.3), `min_confidence` (float, default 0.1), `scope_filter` (optional MemoryScope), `tier_filter` (optional MemoryTier), `branch` (optional str), `dedupe_window` (list of keys already in context, default empty)
- [x] `recall(message: str) -> RecallResult` method that: searches store via retriever, filters by scope/tier/branch/confidence, removes keys in `dedupe_window`, applies safety checks, formats via `inject_memories()` logic, enforces token budget, returns `RecallResult`
- [x] `recall_time_ms` is measured and returned in the result
- [x] When no relevant memories are found, returns empty `RecallResult` (not an error)
- [x] Thread-safe: multiple concurrent `recall()` calls do not corrupt state

---

### STORY-003.3: Implement the capture pipeline

**Status:** done
**Effort:** M
**Depends on:** STORY-003.1
**Context refs:** `src/tapps_brain/extraction.py`, `src/tapps_brain/store.py`, `src/tapps_brain/consolidation.py`
**Verification:** `pytest tests/unit/test_recall.py::TestCapturePipeline -v --cov=tapps_brain.recall --cov-report=term-missing`

#### Why

Auto-recall is only half the loop. The other half is capturing new facts from agent responses and persisting them back to the store. Without capture, the memory store grows stale and recall becomes less useful over time. The existing `ingest_context()` method handles extraction, but there's no pipeline that runs after each agent response.

#### Acceptance Criteria

- [x] `RecallOrchestrator.capture(response: str, source: str = "agent") -> list[str]` method
- [x] Delegates to `store.ingest_context()` for fact extraction
- [x] Returns list of keys for newly created entries
- [x] Deduplication: does not create entries that duplicate existing store content (leverages `ingest_context()` existing dedup)
- [x] Capture is optional — host agents can call `recall()` without ever calling `capture()`
- [x] Unit test: capture a response containing decision patterns, verify entries are created
- [x] Unit test: capture the same response twice, verify no duplicates

---

### STORY-003.4: Add convenience method to MemoryStore

**Status:** done
**Effort:** S
**Depends on:** STORY-003.2
**Context refs:** `src/tapps_brain/store.py`
**Verification:** `pytest tests/unit/test_memory_store.py::TestAutoRecall -v --cov=tapps_brain.store --cov-report=term-missing`

#### Why

Most callers interact with `MemoryStore` directly. A thin convenience method on the store reduces integration friction from "instantiate a RecallOrchestrator, configure it, call recall()" to a single `store.recall(message)` call.

#### Acceptance Criteria

- [x] `MemoryStore.recall(message: str, **kwargs) -> RecallResult` method that creates/caches a `RecallOrchestrator` and delegates
- [x] Accepts optional `RecallConfig` override via kwargs
- [x] Lazy initialization: `RecallOrchestrator` is created on first call, reused after
- [x] Thread-safe: orchestrator creation is guarded by the store lock
- [x] Unit test: `store.recall("what is our tech stack?")` returns a `RecallResult` with relevant memories

---

### STORY-003.5: Integration tests — full recall-inject-capture round-trip

**Status:** done
**Effort:** M
**Depends on:** STORY-003.2, STORY-003.3, STORY-003.4
**Context refs:** `src/tapps_brain/recall.py`, `src/tapps_brain/store.py`
**Verification:** `pytest tests/integration/test_recall_integration.py -v --cov=tapps_brain.recall --cov=tapps_brain.store --cov-report=term-missing`

#### Why

Unit tests validate individual components; integration tests validate the full orchestration loop with a real SQLite-backed store. This catches issues in the wiring between recall, retrieval, injection, capture, and persistence.

#### Acceptance Criteria

- [x] Integration test: populate store with 20 entries across tiers/scopes, call `recall()` with a query that matches 3 entries, verify `RecallResult` contains exactly those 3
- [x] Integration test: call `recall()` with `dedupe_window` containing one of the matching keys, verify it's excluded from results
- [x] Integration test: call `recall()` then `capture()` with a response containing new facts, verify new entries appear in the store
- [x] Integration test: `recall()` with `scope_filter=MemoryScope.PROJECT` excludes session-scoped entries
- [x] Integration test: `recall()` with `branch="feature-x"` includes branch-scoped entries for that branch
- [x] Integration test: token budget enforcement — inject 50 high-scoring entries but limit to 500 tokens, verify truncation
- [x] All tests use real `MemoryStore` + SQLite (no mocks)

---

### STORY-003.6: Documentation and usage examples

**Status:** done
**Effort:** S
**Depends on:** STORY-003.4
**Context refs:** `docs/guides/`
**Verification:** manual review

#### Why

Auto-recall is the highest-impact feature in this project. Clear documentation with copy-paste examples lowers the barrier for host agents to integrate it. Without docs, the feature is invisible.

#### Acceptance Criteria

- [x] `docs/guides/auto-recall.md` created with: overview of the recall loop, quick-start (5 lines of code), configuration reference for `RecallConfig`, architecture diagram (text-based), example integration with a hypothetical Claude Code hook
- [x] Example shows both recall-only and recall+capture usage
- [x] Documents engagement levels and their behavior
- [x] Documents token budget enforcement

## Priority Order

| Order | Story | Effort | Rationale |
|-------|-------|--------|-----------|
| 1 | STORY-003.1 — Protocol + models | S | Foundation: defines the contracts everything else implements |
| 2 | STORY-003.2 — RecallOrchestrator | L | Core feature: the orchestrator that makes auto-recall work |
| 3 | STORY-003.3 — Capture pipeline | M | Completes the recall loop; can be built in parallel with 003.2 |
| 4 | STORY-003.4 — Store convenience method | S | Thin wrapper; depends on orchestrator |
| 5 | STORY-003.5 — Integration tests | M | Validates full round-trip with real SQLite |
| 6 | STORY-003.6 — Documentation | S | Final polish; depends on stable API |

## Dependency Graph

```
003.1 (protocols) ──┬──→ 003.2 (orchestrator) ──┬──→ 003.4 (store method) ──→ 003.5 (integration)
                    │                            │                                     │
                    └──→ 003.3 (capture)     ────┘                            003.6 (docs)
```

Stories 003.2 and 003.3 can be worked in parallel after 003.1 is complete. Story 003.5 depends on 003.2, 003.3, and 003.4. Story 003.6 depends on 003.4 (needs stable API).
