# Improvement program: `features-and-technologies.md` (index)

> Planning index (not a numbered epic). Child epics: **EPIC-042**–**EPIC-051**.

**Source map:** [`docs/engineering/features-and-technologies.md`](../../engineering/features-and-technologies.md)

**Snapshot (2026-04-02):** **EPIC-042** — stories **042.1–042.8** **done** (epic success criteria / eval evidence may still be open in `EPIC-042.md`). **EPIC-044** — **044.1**/**044.2**/**044.5**/**044.6**/**044.7** **done**; **044.3**/**044.4** core shipped plus operator polish: CLI **`maintenance consolidation-threshold-sweep`**, **`profile_seed_version`** on health/stats/native health (NLI / merge-undo / per-group cap backlog in `EPIC-044.md`). **EPIC-050** is **in_progress** (**050.1**/**050.2**/**050.3** **done**; 050.3 WAL checkpoint runbook shipped; lock-scope / async wrapper deferred in epic bodies).

This index links **one epic per major section** of the feature/technology map. Each epic contains **stories per table row** (industry feature category), with **code baseline**, **2026-oriented research notes**, and **implementation acceptance themes** for fix/enhance/improve work.

| Epic | Scope (section) | File |
|------|-----------------|------|
| **EPIC-042** | §1 Retrieval and ranking (RAG-style memory) | [`EPIC-042.md`](EPIC-042.md) |
| **EPIC-043** | §2 Storage, persistence, and schema | [`EPIC-043.md`](EPIC-043.md) |
| **EPIC-044** | §3 Ingestion, deduplication, and lifecycle | [`EPIC-044.md`](EPIC-044.md) |
| **EPIC-045** | §4 Multi-tenant, sharing, and sync **models** | [`EPIC-045.md`](EPIC-045.md) |
| **EPIC-046** | §5 Agent / tool integration | [`EPIC-046.md`](EPIC-046.md) |
| **EPIC-047** | §6 Quality loop, observability, ops | [`EPIC-047.md`](EPIC-047.md) |
| **EPIC-048** | §7 Optional / auxiliary capabilities | [`EPIC-048.md`](EPIC-048.md) |
| **EPIC-049** | §8 Dependency extras (install surface) | [`EPIC-049.md`](EPIC-049.md) |
| **EPIC-050** | §9 Concurrency and runtime model | [`EPIC-050.md`](EPIC-050.md) |
| **EPIC-051** | §10 Cross-cutting review checklist | [`EPIC-051.md`](EPIC-051.md) |

**Row/story parity (each story maps one table row or §10 bullet):** §1 → 8 stories (042.1–042.8); §2 → 7 (043.1–043.7); §3 → 7 (044.1–044.7); §4 → 5 (045.1–045.5); §5 → 3 (046.1–046.3); §6 → 7 (047.1–047.7); §7 → 6 (048.1–048.6); §8 → 7 (049.1–049.7); §9 → 3 (050.1–050.3); §10 → 6 (051.1–051.6).

**Epic/story alignment:** Each epic opens with a **§ table order** line tying story numbers to feature-map rows. **Context refs** use `src/tapps_brain/…` (or `docs/…` for guides). **Verification** is a concrete `pytest` command where a test module exists.

**Execution:** Stories are intentionally **research + spike + implement** sized. Triage into GitHub issues when a story is scheduled; do not treat the full grid as immediate commitment.

**Story block conventions (042–051):** Each story lists **`Context refs:`** with `src/tapps_brain/…` and/or `docs/…` plus **`tests/unit/…` modules that mirror the Verification command** (so agents open code and tests together). **`Verification:`** uses `pytest … -v --tb=short -m "not benchmark"` when automated tests apply; marker-only extras use the same `-v --tb=short` suffix; **doc-only / design-only** stories state that explicitly instead of pytest.

**Conventions:** [`PLANNING.md`](../PLANNING.md)
