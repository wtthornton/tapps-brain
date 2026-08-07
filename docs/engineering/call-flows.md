# Core Call Flows

This document maps the dominant runtime call paths as implemented now.

## 1) Memory save flow

### CLI entry

- `tapps-brain memory save` -> `memory_save_cmd` in `cli.py` -> `store.save`

### MCP entry

- `memory_save` tool in `mcp_server/` (TAP-605) -> `store.save`

### Store pipeline (simplified)

1. Validate scope/source/tier/agent scope and normalize tier/group values.
2. Safety checks and sanitization (`safety.py`).
3. Dedup fast path and reinforcement when matching normalized content exists.
4. Optional contradiction/invalidation handling.
5. Build/merge `MemoryEntry`, compute integrity hash, enforce max-entry cap.
6. Persist write-through to Postgres via `PostgresPrivateBackend.save`.
7. Optional Hive propagation via `PropagationEngine`.
8. Relation extraction/persistence.
9. Optional auto-consolidation trigger — see below.

### Auto-consolidation trigger (save path)

`store.save` enters `_maybe_consolidate` only when **all** of these hold: consolidation
is enabled, the caller did not pass `skip_consolidation=True`, no merge is already in
progress, and the entry's tier is **not** in `consolidation.exempt_tiers` (default
`["architectural"]`). From there `check_consolidation_on_save` → `should_consolidate`
decides what merges:

- **Same-topic path** — `is_same_topic` is *only* "same tier AND ≥50% tag overlap of
  the smaller set" (`similarity.py`). It carries no text signal, so it is **AND-gated**
  with the similarity threshold: a same-topic pair still has to score
  `>= threshold` under `compute_similarity_with_embeddings` to match. Same topic is a
  necessary condition, never a sufficient one. Before that gate existed, three
  `architectural` entries that merely shared tags matched pairwise and merged.
- **Fallback path** — `find_similar` with the same threshold, best-match-first.
- `min_entries` (default **3**) is the count *including* the entry being saved, so the
  merge fires on the third qualifying save. There is **no time window and no write-rate
  signal** — save proximity is irrelevant to the trigger.

Two guards then bound the damage a merge can do:

- **Content-preservation floor** (`MIN_CONTENT_PRESERVATION_RATIO`, 0.6) — a merge
  retaining less than 60% of its sources' summed bytes raises
  `merge_would_lose_content` *before any write*, so no merge row is created and no
  source is superseded. `merge_values` keeps the newest value verbatim plus at most two
  sentences per older source and caps at `MAX_CONSOLIDATED_VALUE_LENGTH` (4096), so
  merging long-form entries destroys content while superseding the originals. Blocked
  merges increment `store.consolidate.blocked_content_loss`.
- **Lost-update guard** — each source's live `value` is compared against the snapshot
  the merge was computed from; a concurrent write aborts and rolls back the merge.

**This applies to the save path only.** `run_periodic_consolidation_scan` groups via
`find_consolidation_groups` → `find_similar` with a real threshold and never had the
same-topic bypass. It shares the preservation floor, and reports refusals separately as
`PeriodicScanResult.blocked_content_loss` rather than as persist failures.

Recovering the originals after a merge: `brain_recall(..., include_sources=True)`
returns the source entries a merge superseded, and
`maintenance consolidation-merge-undo <key>` reverses one merge deterministically.

## 2) Recall flow

### CLI entry

- `tapps-brain recall` -> `store.recall` in `store.py`

### MCP entry

- `memory_recall` tool in `mcp_server/` (TAP-605) -> `store.recall`

### Recall pipeline (simplified)

1. `MemoryStore.recall` routes to `RecallOrchestrator.recall`.
2. `RecallOrchestrator` calls `inject_memories`.
3. `inject_memories` uses `MemoryRetriever.search`.
4. Retriever executes lexical + optional vector-hybrid search (`retrieval.py`).
   - **Lexical path (`_get_candidates`):** `MemoryStore.search` → `PostgresPrivateBackend.search` runs a `tsvector` full-text query (`plainto_tsquery`); empty/whitespace queries or a query that escapes to nothing yield **no FTS rows**.
   - **When FTS returns hits:** BM25 scores **only those rows** via `_bm25_score_entries`, but the in-process BM25 index is still built over the **full project corpus** (`store.list_all()` without group) so **IDF** matches the whole store (not just the FTS hit set).
   - **Full-corpus BM25 scan:** Used when FTS returns **no** rows, when `store.search` **raises** (logged `fts_search_failed`), or when the backend FTS call fails and returns `[]`. Then `_bm25_full_scan` uses `store.list_all(memory_group=…)` and keeps documents with BM25 score > 0.
   - **Further fallbacks:** BM25 exceptions → word overlap on candidates; full-scan failure → `_like_search` (simple overlap). Hybrid mode runs this BM25 channel in parallel with vector KNN / batch similarity and merges with RRF (`fusion.py`).
5. Composite scoring and filtering applied.
6. Injection formatting applies safety and token budget.
7. Optional Hive merge (local + `universal` + profile namespace), re-sort.
8. Optional post-filters (scope/tier/branch/group/dedupe).
9. Return `RecallResult` with metadata and diagnostics.

## 3) Hive propagation flow

### Write side

- Save path carries `agent_scope` (`private`, `domain`, `hive`).
- `PropagationEngine.propagate` resolves effective scope using profile rules:
  - `private_tiers` force private
  - `auto_propagate_tiers` can promote private -> domain
- Destination namespace:
  - `hive` -> `universal`
  - `domain` -> profile namespace

### Read side

- Recall queries local store first.
- If Hive attached, recall queries Hive namespaces and merges weighted results.

## 4) Federation flow

- Federation is explicit and sync-oriented.
- Projects publish selected entries to federated hub (hub rows include optional publisher **`memory_group`** — GitHub **#51** / 49-E).
- Projects pull synced entries from subscriptions; import restores **`memory_group`** on local saves when present.
- No automatic background cross-project propagation in core path.

## 5) Maintenance flow

### Migrate

- `maintenance migrate` opens store; migrations run on store open.

### Consolidation

- `maintenance consolidate` -> consolidation scan/merge routines.
- `maintenance consolidation-threshold-sweep` -> read-only report from `evaluation.run_consolidation_threshold_sweep` (no mutations; optional `--thresholds`, `--json`).

### GC

- `maintenance gc` -> stale candidate detection -> archive -> delete.

### Health and diagnostics

- `maintenance health` -> `store.health` (includes `profile_seed_version` when `profile.seeding.seed_version` is set)
- diagnostics/flywheel commands -> deterministic quality loop services
