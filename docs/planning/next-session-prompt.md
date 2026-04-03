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
- **EPIC-044.4 (consolidation):** JSONL `consolidation_merge` / `consolidation_source` on auto-merge (save + periodic scan); **`MemoryStore.undo_consolidation_merge`** / **`undo_consolidation_merge`** + audit **`consolidation_merge_undo`**; CLI **`tapps-brain maintenance consolidation-merge-undo CONSOLIDATED_KEY`** (`--json`); **`evaluation.run_consolidation_threshold_sweep`** + report models; CLI **`tapps-brain maintenance consolidation-threshold-sweep`** (`--json`, optional `--thresholds` / `--min-group-size` / `--include-contradicted`). Consolidated row **`save(..., skip_consolidation=True)`** avoids recursive merge-on-save.
- **EPIC-044.5 (GC):** `MemoryStore.gc` / CLI / MCP — dry-run **`reason_counts`**, **`estimated_archive_bytes`**, live **`archive_bytes`**; counters **`store.gc.archived`** / **`store.gc.archive_bytes`**; **`StoreHealthReport`** **`gc_runs_total`**, **`gc_archived_rows_total`**, **`gc_archive_bytes_total`**; canonical **`archive.jsonl`** under store memory dir.
- **EPIC-044.6 (seeding):** **`MemoryProfile.seeding.seed_version`**; **`seed_from_profile`** / **`reseed_from_profile`** include **`profile_seed_version`** when set; same field on **`StoreHealthReport`**, **`maintenance health`**, native **`run_health_check.store.profile_seed_version`**, MCP **`memory://stats`**; **`seeding`** module documents **`conflict_check`** on seed saves.
- **EPIC-044.7 (caps):** Global + optional **`limits.max_entries_per_group`** (per-`memory_group` bucket + ungrouped; fair global eviction when set) in **`docs/engineering/data-stores-and-schema.md`**; **`StoreHealthReport.max_entries_per_group`**, MCP **`memory://stats`**, native health, CLI **`store stats`**.
- **EPIC-044.3 (save-path conflicts):** `exclude_key`; `SaveConflictHit` (entry + similarity); invalidation sets `contradicted` + `contradiction_reason` (`format_save_conflict_reason`); `profile.conflict_check` / `ConflictCheckConfig`; structured log `memory_save_conflicts_detected` includes `similarity_threshold` and `conflicts`. **Offline:** `evaluation.run_save_conflict_candidate_report`, CLI `maintenance save-conflict-candidates`, guide [`save-conflict-nli-offline.md`](../guides/save-conflict-nli-offline.md) (no NLI on sync save). Optional async/product NLI wiring remains backlog.
- **EPIC-051 (complete):** Cross-cutting **§10** checklist — [`EPIC-051.md`](epics/EPIC-051.md); ADRs **001**–**006** in [`adr/`](adr/) ([`ADR-001`](adr/ADR-001-retrieval-stack.md) retrieval … [`ADR-006`](adr/ADR-006-save-path-observability.md) save-path observability stance). **051.5** expanded [`sqlcipher.md`](../guides/sqlcipher.md). **Roadmap row 20** `save_phase_summary` — **done** (see **ADR-006** for deferrals beyond shipped metrics).
- **Docs:** [`embedding-model-card.md`](../guides/embedding-model-card.md) § *Performance review backlog* — triage table, not a code mandate.
- **Tests:** `test_concurrent_save_all_persisted` uses **60s** join / elapsed bound (Windows full-suite stability).
- **examples/brain-visual:** Demo HTML + help UX for operators (Hive / entries / DB tiles); see `examples/brain-visual/README.md`.

**Backlog-by-default (execute unless a trigger fires):** **B** (save-path metrics **beyond** [`ADR-006`](adr/ADR-006-save-path-observability.md) shipped surface), **C** (EPIC-042 hygiene), and **in-product NLI/async** conflict wiring stay **backlogged** until a human or milestone explicitly invokes a trigger in [`PLANNING.md` § Optional backlog gating](PLANNING.md#optional-backlog-gating): (a) save-latency tuning/incident → B; (b) epic/GitHub closure needed → C; (c) explicit product requirement for NLI review → separate surface, never sync `save`.

**When not triggered:** Pick other roadmap/epic work (new issues, **EPIC-050** lock-scope only with benchmarks, **row 22** modularization design-first, **EPIC-032** defer, etc.).

**Reference slices (do not assume scheduled):**

| Ref | Slice | Typical outcome when pulled |
|-----|--------|----------------------------|
| A | **STORY-044.3 remainder** | Offline export **on `main`**; further = product NLI/async only with trigger (c). |
| B | **Save-path observability (beyond ADR-006)** | Extra metrics/logs vs consolidation/GC — trigger **(a)** ([`ADR-006`](adr/ADR-006-save-path-observability.md) records shipped baseline). |
| C | **EPIC-042 hygiene** | Eval evidence + GitHub — trigger (b). |

**Long horizon / defer:** **EPIC-032** OTel GenAI. **EPIC-050** lock-scope without benchmarks. **Tracking table row 22** — MemoryStore modularization; design-first only.

**Your task — pick ONE primary slice** (one PR unless trivial), run the epic’s verification command, then update `docs/planning/epics/…`, `open-issues-roadmap.md` (changelog + last updated if needed), `STATUS.md` if the queue changes, and refresh **this file’s** “Already on main” if you shipped something listed above.

**Quality bar:** `ruff check` / `ruff format` on touched paths; `mypy --strict src/tapps_brain/` on touched modules; full gate as in `CLAUDE.md` if you touch core widely.

---

*File purpose: paste-the-prompt handoff. Last synced: 2026-04-03 — **EPIC-051** complete (ADR-001–006); **B** = metrics beyond **ADR-006** only with trigger **(a)**; **C / NLI** gated per `PLANNING.md` § Optional backlog gating.*
