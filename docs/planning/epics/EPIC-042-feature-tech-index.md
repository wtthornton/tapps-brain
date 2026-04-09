# Improvement program: `features-and-technologies.md` (index)

> Planning index (not a numbered epic). Child epics: **EPIC-042**–**EPIC-051**.

**Source map:** [`docs/engineering/features-and-technologies.md`](../../engineering/features-and-technologies.md)

**Snapshot (2026-04-09):** **EPIC-042** — **done** (stories 042.1–042.8 complete; success criteria checked). **EPIC-044** — **done** (044.1–044.7 complete; 044.3 core + offline conflict export shipped; optional product NLI backlog). **EPIC-050** — **done** (050.1–050.3; lock-scope / async wrapper deferred). **EPIC-051** — **done**; §10 checklist ADR-001–ADR-006. **EPIC-053** — **done** (v3.1.0): per-agent brain identity, isolated `{project}/.tapps-brain/agents/{agent_id}/memory.db`, auto-registration, `maintenance split-by-agent`. **EPIC-054** — **done** (v3.1.0): `HiveBackend`/`FederationBackend`/`AgentRegistryBackend` protocols; `create_hive_backend()`/`create_federation_backend()` factories; `SqliteHiveBackend`/`SqliteFederationBackend` adapters. **EPIC-055** — **done** (v3.1.0): `PostgresHiveBackend` (pgvector, tsvector, LISTEN/NOTIFY, connection pooling); `PostgresFederationBackend`; SQL migrations in `migrations/`; conformance tests; CLI `migrate-hive`/`hive-schema-status`. **EPIC-056** — **done** (v3.1.0): declarative groups + expert auto-publish; `MemoryStore(groups=[], expert_domains=[])`. **EPIC-057** — **done** (v3.1.0): `AgentBrain` facade — `remember()`, `recall()`, `forget()`, `learn_from_success/failure()`; simplified MCP/CLI aliases. **EPIC-058** — **done** (v3.1.0): Docker deployment, `docker-compose.hive.yaml`, Hive-aware health checks, `maintenance backup-hive`/`restore-hive`.

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
| **EPIC-053** | Per-agent brain identity (§2, §4, §9) | [`EPIC-053.md`](EPIC-053.md) |
| **EPIC-054** | Hive backend abstraction layer (§2, §4) | [`EPIC-054.md`](EPIC-054.md) |
| **EPIC-055** | PostgreSQL Hive & Federation backend (§2, §4, §9) | [`EPIC-055.md`](EPIC-055.md) |
| **EPIC-056** | Declarative group membership & expert publishing (§4) | [`EPIC-056.md`](EPIC-056.md) |
| **EPIC-057** | Unified Agent API — AgentBrain facade (§5) | [`EPIC-057.md`](EPIC-057.md) |
| **EPIC-058** | Docker & deployment support (§5) | [`EPIC-058.md`](EPIC-058.md) |

**Row/story parity (each story maps one table row or §10 bullet):** §1 → 8 stories (042.1–042.8); §2 → 7 (043.1–043.7); §3 → 7 (044.1–044.7); §4 → 5 (045.1–045.5); §5 → 3 (046.1–046.3); §6 → 7 (047.1–047.7); §7 → 6 (048.1–048.6); §8 → 7 (049.1–049.7); §9 → 3 (050.1–050.3); §10 → 6 (051.1–051.6). **Multi-section epics (053–058):** cross-cut §2/§4/§5/§9 — see individual epics for story counts.

**Epic/story alignment:** Each epic opens with a **§ table order** line tying story numbers to feature-map rows. **Context refs** use `src/tapps_brain/…` (or `docs/…` for guides). **Verification** is a concrete `pytest` command where a test module exists.

**Execution:** Stories are intentionally **research + spike + implement** sized. Triage into GitHub issues when a story is scheduled; do not treat the full grid as immediate commitment.

**Story block conventions (042–051):** Each story lists **`Context refs:`** with `src/tapps_brain/…` and/or `docs/…` plus **`tests/unit/…` modules that mirror the Verification command** (so agents open code and tests together). **`Verification:`** uses `pytest … -v --tb=short -m "not benchmark"` when automated tests apply; marker-only extras use the same `-v --tb=short` suffix; **doc-only / design-only** stories state that explicitly instead of pytest.

**Conventions:** [`PLANNING.md`](../PLANNING.md)
