# Save conflicts: offline review and NLI backlog

Save-time conflict detection uses deterministic text similarity (`detect_save_conflicts` in `contradictions.py`) when `MemoryStore.save(..., conflict_check=True)` runs. **No neural model or LLM runs on that synchronous path.**

## Exporting candidate pairs

Operators can dump the same pairs the save path would flag:

```bash
tapps-brain maintenance save-conflict-candidates --project-dir . --json
```

- **Threshold:** Defaults to `profile.conflict_check` (aggressiveness or `similarity_threshold`), or the built-in medium tier if the profile has no `conflict_check`. Override with `--threshold 0.55`.
- **Scope:** By default, only non-contradicted, non-consolidated rows are treated as hypothetical *incoming* saves; the scan corpus is still the full store (matching `MemoryStore.save`). Pass `--include-contradicted` to include every row as hypothetical incoming.
- **Implementation:** `evaluation.run_save_conflict_candidate_report` — read-only, intended for maintenance, not hot paths (cost grows with store size).

Use `--json` output as input to your own batch job (CSV/JSONL converters, Hugging Face NLI, cloud APIs, etc.). Label results offline; do not wire silent model calls into `MemoryStore.save`.

## Async / worker pattern

If product needs NLI-backed decisions:

1. Keep **sync save** on heuristics only (current behavior).
2. Run **async workers** or scheduled jobs that read exports or query the store read-only, append labels to a sidecar dataset, or feed review queues.
3. Apply policy changes through explicit human or batch workflows — not automatic invalidation from an embedded model on save.

## See also

- `profile.conflict_check` / `ConflictCheckConfig` in `profiles.md`
- EPIC-044 STORY-044.3 in `docs/planning/epics/EPIC-044.md`
