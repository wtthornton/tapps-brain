# Next session — agent handoff prompt

Copy everything below the line into a new chat (or Ralph task) as the **user message**.

---

**Project:** tapps-brain (SQLite-backed memory for AI assistants; sync Python core; optional `[vector]`, `[encryption]`).

**Start by reading:** `CLAUDE.md`, `docs/planning/open-issues-roadmap.md`, `docs/planning/STATUS.md`, then the epic you implement (`docs/planning/epics/EPIC-042.md` and/or `EPIC-050.md`). Canonical product queue is the **open-issues roadmap**, not `.ralph/fix_plan.md`, unless you are explicitly running Ralph.

**Already on `main` (do not redo):**

- **EPIC-042:** Stories **042.1, 042.2, 042.4, 042.5, 042.7, 042.8** = **done** (042.2: model card, min-max relevance norm, int8 spike helpers in `embeddings.py`, schema **v17** `embedding_model_id` + store embed path; **042.4:** RRF formula + citation in `fusion.py`, `HybridFusionConfig` / `profile.hybrid_fusion`, `inject_memories` wiring).
- **EPIC-050 STORY-050.3:** Opt-in read-only SQLite for FTS + sqlite-vec KNN — `TAPPS_SQLITE_MEMORY_READONLY_SEARCH`, `connect_sqlite_readonly` (`sqlcipher_util.py`), `MemoryPersistence`; docs: `docs/guides/sqlite-database-locked.md`, `docs/engineering/system-architecture.md`.
- **Conflict fix:** `detect_save_conflicts(..., exclude_key=key)` (`contradictions.py`, `store.py`); noted under **EPIC-044** STORY-044.3.
- **Roadmap row 20:** `save_phase_summary` on store health / MCP (live store) — **done**; no need to re-implement.
- **Docs:** [`embedding-model-card.md`](../guides/embedding-model-card.md) § *Performance review backlog* — **not** implementation work; table of optional perf/schema follow-ups for triage.
- **Tests:** `test_concurrent_save_all_persisted` uses **60s** join / elapsed bound (full-suite Windows stability).

**Your task — pick ONE primary slice (one PR unless trivial):**

1. **STORY-042.3:** sqlite-vec **operator playbook** (rebuild/vacuum), **incremental index** cost notes, **distance metric** vs real SQL (`sqlite_vec_index.py`, `persistence.py`). Verify: `pytest tests/ -k sqlite_vec -v --tb=short -m "not benchmark"`.

2. **STORY-042.6:** Rerank **observability** (latency, provider, candidate count) after hybrid recall (`reranker.py`, injection). Verify: `pytest tests/unit/test_reranker.py tests/unit/test_memory_retrieval.py -v --tb=short -m "not benchmark"`.

3. **EPIC-044.3 remainder:** User-visible conflict **reason**; profile **aggressiveness** for `detect_save_conflicts`.

4. **EPIC-050 optional:** WAL **checkpoint** note for long-lived MCP in runbook; lock-scope reduction stays deferred unless benchmark-driven.

**Quality bar:** `ruff check` / `ruff format` on touched paths; `mypy --strict src/tapps_brain/` on touched modules; full gate as in `CLAUDE.md` if you touch core widely.

**After shipping:** Update `docs/planning/epics/…`, `open-issues-roadmap.md` (changelog + last updated if needed), and `STATUS.md` if epic/table rows change. Refresh this file’s “Already on main” section if you complete a listed slice.

---

*File purpose: paste-the-prompt handoff. Last synced with planning docs: 2026-04-02 (042.4 shipped; STATUS + handoff queue).*
