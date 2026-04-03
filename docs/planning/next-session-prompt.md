# Next session — agent handoff prompt

Copy everything below the line into a new chat (or Ralph task) as the **user message**.

---

**Project:** tapps-brain (SQLite-backed memory for AI assistants; sync Python core; optional `[vector]`, `[encryption]`).

**Start by reading:** `CLAUDE.md`, `docs/planning/open-issues-roadmap.md`, `docs/planning/STATUS.md`, then the epic you implement. Canonical product queue is the **open-issues roadmap**, not `.ralph/fix_plan.md`, unless you are explicitly running Ralph.

**Already on `main` (do not redo):**

- **EPIC-042:** Stories **042.1–042.8** = **done** (rerank observability, sqlite-vec ops doc, `HybridFusionConfig`, v17 `embedding_model_id`, etc.). Epic-level success criteria in `EPIC-042.md` may still list open checkboxes (eval / GitHub hygiene).
- **EPIC-050 STORY-050.3 (code + triage doc):** Opt-in read-only SQLite for FTS + sqlite-vec KNN — `TAPPS_SQLITE_MEMORY_READONLY_SEARCH`, `connect_sqlite_readonly`, `MemoryPersistence`; [`sqlite-database-locked.md`](../guides/sqlite-database-locked.md) (incl. § *WAL checkpoint* for long-lived MCP); [`openclaw-runbook.md`](../guides/openclaw-runbook.md) § *Long-lived MCP and SQLite WAL*; [`system-architecture.md`](../engineering/system-architecture.md) § concurrency.
- **EPIC-044.1 (RAG safety):** `profile.safety` / `SafetyConfig.ruleset_version`; `check_content_safety(..., ruleset_version=, metrics=)`; `DEFAULT_SAFETY_RULESET_VERSION`, `resolve_safety_ruleset_version`, `SafetyCheckResult.ruleset_version`; metrics `rag_safety.blocked` / `rag_safety.sanitized`; `StoreHealthReport` `rag_safety_*`; save blocks on any `safe=False`; injection uses sanitised text when applicable.
- **EPIC-044.2 (Bloom + dedup normalize):** `normalize_for_dedup` applies **NFKC** + lower + whitespace; `bloom_false_positive_probability`, `BloomFilter.approximate_false_positive_rate`, `bit_size` / `hash_count`; module doc describes nominal FP at `expected_items` / `fp_rate`.
- **EPIC-044.4 (consolidation):** JSONL `consolidation_merge` / `consolidation_source` on auto-merge (save + periodic scan); **`evaluation.run_consolidation_threshold_sweep`** + report models for threshold sensitivity (no store mutations). Merge **undo** remains epic backlog.
- **EPIC-044.5 (GC):** `MemoryStore.gc` / CLI / MCP — dry-run **`reason_counts`**, **`estimated_archive_bytes`**, live **`archive_bytes`**; counters **`store.gc.archived`** / **`store.gc.archive_bytes`**; **`StoreHealthReport`** **`gc_runs_total`**, **`gc_archived_rows_total`**, **`gc_archive_bytes_total`**; canonical **`archive.jsonl`** under store memory dir.
- **EPIC-044.6 (seeding):** **`MemoryProfile.seeding.seed_version`**; **`seed_from_profile`** / **`reseed_from_profile`** include **`profile_seed_version`** when set; **`seeding`** module documents **`conflict_check`** on seed saves.
- **EPIC-044.7 (caps):** Formal eviction policy in **`docs/engineering/data-stores-and-schema.md`** (linked from features map + **`profiles.md`**). Per-group caps backlog in epic.
- **EPIC-044.3 (save-path conflicts):** `exclude_key`; `SaveConflictHit` (entry + similarity); invalidation sets `contradicted` + `contradiction_reason` (`format_save_conflict_reason`); `profile.conflict_check` / `ConflictCheckConfig`; structured log `memory_save_conflicts_detected` includes `similarity_threshold` and `conflicts`. NLI / richer UX remains backlog inside the epic.
- **Roadmap tracking row 20:** `save_phase_summary` on live store health / MCP — **done**.
- **Docs:** [`embedding-model-card.md`](../guides/embedding-model-card.md) § *Performance review backlog* — triage table, not a code mandate.
- **Tests:** `test_concurrent_save_all_persisted` uses **60s** join / elapsed bound (Windows full-suite stability).

**What’s next (recommended order):**

1. **EPIC-044 — backlog only:** merge **undo**, **044.3** NLI/async research, optional **per-group** entry caps — see [`EPIC-044.md`](epics/EPIC-044.md). Core **044.1**–**044.7** themes shipped on `main` (2026-04-02).

2. **Optional observability:** Roadmap “save-path / auto-consolidation metrics” and **EPIC-051 STORY-051.6** (*Save-path observability*) — correlate latency with consolidation/GC; structured logging or metrics only if you explicitly take this slice.

3. **Long horizon / defer:** **EPIC-032** OTel GenAI semantic conventions. **EPIC-051** other stories (retrieval alternatives, freshness, scale, security checklist). **EPIC-050** lock-scope reduction + optional `tapps_brain_async` — deferred unless benchmark or demand. **Tracking table row 22** — MemoryStore modularization; design-first only.

4. **Hygiene (non-blocking):** **EPIC-042** epic success criteria — offline eval evidence, GitHub closure — process/docs.

**Your task — pick ONE primary slice** (one PR unless trivial), run the epic’s verification command, then update `docs/planning/epics/…`, `open-issues-roadmap.md` (changelog + last updated if needed), `STATUS.md` if the queue changes, and refresh **this file’s** “Already on main” if you shipped something listed above.

**Quality bar:** `ruff check` / `ruff format` on touched paths; `mypy --strict src/tapps_brain/` on touched modules; full gate as in `CLAUDE.md` if you touch core widely.

---

*File purpose: paste-the-prompt handoff. Last synced: 2026-04-02 — queue: EPIC-044 research/backlog → EPIC-050 lock-scope/async deferred → optional 051.6 / EPIC-032 deferred.*
