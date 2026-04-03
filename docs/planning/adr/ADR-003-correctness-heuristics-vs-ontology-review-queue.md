# ADR-003: Correctness — heuristic conflicts + offline review (defer ontology and in-product review queue)

**Status:** Accepted  
**Date:** 2026-04-03  
**Owner:** @wtthornton  
**Epic / story:** [EPIC-051](../epics/EPIC-051.md) — STORY-051.3  
**Context:** [features-and-technologies.md](../../engineering/features-and-technologies.md) section 10 checklist item 3

## Context

Checklist item 3 asks whether **save-time heuristic conflicts** are sufficient or the product needs a **stronger ontology** and/or **human review queues** (often exposed via MCP in enterprise knowledge bases).

Shipped today:

- **Save-path conflicts (EPIC-044.3):** `contradictions.py` exposes `detect_save_conflicts` / `SaveConflictHit`; `MemoryStore.save(..., conflict_check=True)` can **invalidate** prior rows (`contradicted`, `contradiction_reason`) using profile **`ConflictCheckConfig`** / similarity thresholds — **deterministic**, no LLM on the sync save path.
- **Offline analysis:** `evaluation.run_save_conflict_candidate_report`, CLI `maintenance save-conflict-candidates`, and [`save-conflict-nli-offline.md`](../../guides/save-conflict-nli-offline.md) support **optional** NLI or manual review **outside** `MemoryStore.save`.
- **Feedback:** `feedback.py` / `FeedbackStore` records recall/gap/issue-style events for the quality loop — not a structured “pending contradiction” work queue.
- **Legacy checks:** Additional contradiction detectors (e.g. tech stack / file patterns) live in `contradictions.py` where integrated.

**Planning policy:** In-product **NLI / async conflict wiring** (MCP, worker, in-app model) stays **backlogged by default** until trigger **(c)** in [`PLANNING.md`](../PLANNING.md) (*explicit product requirement for NLI-assisted conflict review*), with **no** silent LLM on sync save.

## Decision

1. **Shipped / maintained path (do):** Keep **heuristic, deterministic** save-time conflict detection and invalidation as the **core** correctness boundary. Continue to support **offline / operator-driven** conflict candidate export and documentation for **opt-in** external review (including NLI) **without** changing the sync save contract.

2. **Out of scope for core / deferred (not shipping now):**
   - A **curated ontology** or enterprise truth layer as a **built-in** product subsystem.
   - **First-class human review queue** in the store (e.g. automatic `needs_review` tagging from every conflict hit, workflow states, SLA fields).
   - **New MCP tools** to **list** or **resolve** “pending contradictions” as a dedicated work queue — **deferred** until a **written product spec** exists **and** backlog trigger **(c)** (or equivalent stakeholder commitment) explicitly authorizes an **opt-in** surface (still **no** LLM on sync `save`).

3. **Wontfix for “v2 core” as mandatory features:** Requiring stronger ontology or in-app review queues **by default** for all installs — rejected for core scope; reassess only under new product positioning.

Revisit with a **new** epic/story when a customer or internal product line requires **workflow-grade** contradiction triage; scope interfaces, state machine, and MCP contracts in that follow-up.

## Consequences

- **`contradictions.py` / `store.save`:** Remain the canonical save-path conflict behavior; no new mandatory tags or queues from this ADR.
- **`feedback.py`:** Unchanged contract; optional future events could reference conflict keys, but no new queue semantics without a follow-up spec.
- **Operators and integrators** use **CLI maintenance**, **JSONL audit**, **offline reports**, and **feedback** for observability — not a bundled review UI in core.

## References

- [`features-and-technologies.md`](../../engineering/features-and-technologies.md) — section 3 (contradiction row), section 10 checklist.
- [`save-conflict-nli-offline.md`](../../guides/save-conflict-nli-offline.md) — offline NLI / export path.
- [`PLANNING.md`](../PLANNING.md) — *Optional backlog gating*, trigger **(c)**.
- [`EPIC-051.md`](../epics/EPIC-051.md) — STORY-051.3.
