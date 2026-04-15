# tapps-brain Cleanup & Simplification Plan

> **ARCHIVED — pre-ADR-007 (SQLite era).** This document was written on 2026-04-08 when
> tapps-brain still used SQLite. ADR-007 (2026-04-11) removed SQLite entirely and replaced
> it with PostgreSQL-only persistence. All SQLite, sqlite-vec, SQLCipher, FTS5, and
> `persistence.py` references below are historical and no longer apply to the codebase.
> See [ADR-007](docs/planning/adr/ADR-007-postgres-only-no-sqlite.md) for the rationale
> and [CHANGELOG.md](CHANGELOG.md) for what was actually shipped.

**Date:** 2026-04-08  
**Scope:** First production release — no backwards compatibility needed  
**Principle:** One best way to do each thing. Remove all optionality, legacy paths, and dead code.  
**Research:** All recommendations validated against 2026 ecosystem state (April 2026).

---

## Table of Contents

1. [Dead Code Removal](#1-dead-code-removal)
2. [Feature Flags Cleanup](#2-feature-flags-cleanup)
3. [Backwards Compatibility Removal](#3-backwards-compatibility-removal)
4. [Third-Party Dependencies: Make Mandatory](#4-third-party-dependencies-make-mandatory)
5. [Consolidate Dual/Triple Options](#5-consolidate-dualtriple-options)
6. [Schema Migration Collapse](#6-schema-migration-collapse)
7. [Environment Variable Cleanup](#7-environment-variable-cleanup)
8. [Optional Dependency Simplification](#8-optional-dependency-simplification)
9. [Dependency Version Pin Updates](#9-dependency-version-pin-updates)
10. [Embedding Model Upgrade](#10-embedding-model-upgrade)
11. [Reranker: Add Local Default](#11-reranker-add-local-default)
12. [SQLite Best Practices](#12-sqlite-best-practices)
13. [Interface Boilerplate Reduction](#13-interface-boilerplate-reduction)
14. [Adopt PEP 735 Dependency Groups](#14-adopt-pep-735-dependency-groups)
15. [Estimated Impact](#15-estimated-impact)
16. [Implementation Order](#16-implementation-order)

---

## 1. Dead Code Removal

### 1a. Remove 4 unused algorithm modules (~450 lines)

These modules are tested but **never called** by any production code path:

| Module | Lines | Why it exists | Why remove |
|--------|-------|---------------|------------|
| `rake.py` | 87 | RAKE keyword extraction — planned but never integrated | No callers in src/ |
| `textrank.py` | 185 | TextRank summarization — planned but never integrated | No callers in src/ |
| `pagerank.py` | 71 | PageRank graph algo — redundant (textrank has its own copy) | No callers, duplicate |
| `louvain.py` | 106 | Community detection — planned for #33 (relations graph) | No callers in src/ |

**Action:** Delete all 4 files + their test files (`test_rake.py`, `test_textrank.py`, `test_pagerank.py`, `test_louvain.py`). Remove from `__init__.py` exports if present.

### 1b. Remove FAISS optional dependency (completely unused)

FAISS is declared as an optional extra in `pyproject.toml` but is **never imported or used anywhere**:

- `_feature_flags.py` detects it but no code ever checks `feature_flags.faiss`
- `sqlite-vec` is the actual vector backend (core dependency)
- No FAISS index code, no FAISS import, no FAISS conditional path
- **2026 research confirms:** At <100K vectors, sqlite-vec brute-force KNN is fast (milliseconds). FAISS only wins at >1M vectors with optimized ANN indexes. sqlite-vec is the correct choice for a per-user memory store.

**Action:**
- Remove `faiss = ["faiss-cpu>=1.13.2,<2"]` from `pyproject.toml` optional deps
- Remove `faiss` from the `all` extra
- Remove `faiss` property from `_feature_flags.py`
- Remove any FAISS references in README/docs

### 1c. Remove `migration.py` — legacy memory-core import (359 lines)

This module exists solely to import data from a **prior product** (`memory-core`). Since this is the first production release, there is no installed base to migrate from.

- Searches for `~/.openclaw/memory/` databases
- Auto-detects table/column names from an unknown schema
- Tags entries as `migrated-from-memory-core`

**Action:** Delete `migration.py` and its test file. Remove CLI commands that reference it (`migrate`, `import-workspace` etc.). Remove from `__init__.py` exports.

---

## 2. Feature Flags Cleanup

### Current state: 8 flags, only 3 actually used

| Flag | Used? | Action |
|------|-------|--------|
| `faiss` | Never checked | **Remove** |
| `numpy` | Never checked | **Remove** |
| `sentence_transformers` | Yes (embeddings.py, health_check.py) | **Remove** — make mandatory (see §4) |
| `anthropic_sdk` | Yes (evaluation.py) | Keep — legitimately optional for LLM-as-judge |
| `openai_sdk` | Yes (evaluation.py) | Keep — legitimately optional for LLM-as-judge |
| `otel` | Yes (otel_exporter.py) | **Remove** — see §4e, `opentelemetry-api` becomes core |
| `sqlite_vec` | Never directly checked | **Remove** — already core dependency |
| `memory_semantic_search` | Derived, always == `sentence_transformers` | **Remove** — redundant |

**Action:** Reduce `_feature_flags.py` from 8 flags to 2: `anthropic_sdk`, `openai_sdk`. Remove `as_dict()` entries for removed flags. Consider whether `_feature_flags.py` is even worth keeping for just 2 flags, or inline the checks at the 2 call sites in `evaluation.py`.

---

## 3. Backwards Compatibility Removal

### 3a. Legacy tier enum + profile tier union (~40 lines across 2 files)

Both `mcp_server.py:212-216` and `cli.py:419-423` compute:
```python
_all_valid_tiers = _legacy_tiers | _profile_tiers
```

Since this is the first release, the profile system IS the tier system. There is no "legacy" MemoryTier enum that diverges from profiles.

**Action:**
- Profiles define tiers. The `MemoryTier` enum should be the **default layer names** used by the `repo-brain.yaml` profile. Validation should use `profile.layer_names` only — no union with a separate enum.
- Remove `_legacy_tiers` computations from both `mcp_server.py` and `cli.py`

### 3b. Decay config legacy field mapping (~50 lines)

`decay.py:391-442` maps old `*_half_life_days` attribute names to new layer-based config:
```python
legacy_map = {
    "architectural": "architectural_half_life_days",
    "pattern": "pattern_half_life_days",
    ...
}
```

**Action:** Remove the entire `legacy_map` block. Half-lives come from the profile's `LayerDefinition.half_life_days` — there is no need for a separate top-level attribute per tier.

### 3c. Pydantic AliasChoices for old field names

`profile.py:225-233` supports both old and new names:
- `top_bm25` aliases `top_k_lexical`
- `top_vector` aliases `top_k_dense`

**2026 Pydantic research:** `AliasChoices` remains a supported pattern, but for a first release with no existing configs to migrate, aliases add unnecessary complexity. Pydantic v2.11+ also added `validate_by_name=True` in ConfigDict for fine-grained control — but we don't need any of this for a clean launch.

**Action:** Pick the best name for each and remove `AliasChoices`:
- **Use `top_k_lexical` and `top_k_dense`** (clearer, self-documenting). Rename the fields, update all YAML profiles and code references. Remove `AliasChoices` import if no longer needed.

### 3d. Memory relay scope migration (~35 lines)

`memory_relay.py:56-89` handles a legacy `scope` field that predates the current `visibility` + `agent_scope` split.

**Action:** Remove legacy `scope` fallback. Only accept `visibility` and `agent_scope` fields. Simplify `resolve_relay_scopes()` to only handle current field names.

### 3e. Profile tier migration utility (`profile_migrate.py` — 110 lines)

Exists to migrate entries from one tier name to another (e.g., when switching profiles). For a first release, there is nothing to migrate.

**Action:** Delete `profile_migrate.py` and its test file. Remove CLI commands and MCP tools that reference tier migration. Can be re-added later if profile switching becomes a real user need.

### 3f. Encryption migration utility (`encryption_migrate.py` — 82 lines)

Provides `encrypt_plain_database()`, `decrypt_to_plain_database()`, `rekey_database()`. These are for migrating existing unencrypted databases to encrypted ones. For a first release, users choose encryption at creation time.

**Action:** Delete `encryption_migrate.py` and its test file. Remove CLI commands that reference encrypt/decrypt migration. Keep the core `sqlcipher_util.py` for creating encrypted databases from scratch.

### 3g. Reranker Cohere v1/v2 compatibility shim

`reranker.py:120-137` checks `hasattr(cohere, "ClientV2")` to support both old and new Cohere SDK versions.

**2026 research:** Cohere SDK is at v5.21.1. `ClientV2` is the standard/recommended client. `Client` (v1) still exists for backwards compat but `ClientV2` is what Cohere documents.

**Action:** Remove the `hasattr` check. Use `cohere.ClientV2` directly since we pin `cohere>=5.0`.

### 3h. Unknown tier/source fallback logging

`decay.py:150-154, 182-184` logs warnings and uses fallback values for unknown tiers/sources. In a clean first release, unknown tiers/sources should be a hard validation error, not a silent fallback.

**Action:** Replace fallback logging with `ValueError` raises. Invalid tiers/sources should fail fast.

### 3i. FTS5 fallback to LIKE search

`federation.py:532-552` and `hive.py:846` fall back to `LIKE` queries when FTS5 fails.

**2026 research:** FTS5 is stable and universally available in all Python 3.12+ SQLite builds (minimum SQLite 3.15.2 guaranteed). There is no scenario where FTS5 is unavailable.

**Action:** Remove LIKE fallback paths. FTS5 is always present.

### 3j. Persistence schema version guards

`persistence.py` and `store.py` have multiple `if self._schema_version < N:` guards for conditional behavior. Since this is the first release, all databases start at the current schema version.

**Action:** Remove all schema version guards. The code should assume the current schema version. (See also §6 on collapsing migrations.)

---

## 4. Third-Party Dependencies: Make Mandatory

### 4a. `sentence-transformers` — already core, formalize it

Currently: Core dependency in `pyproject.toml`, but `embeddings.py` still treats it as optional with feature-flag detection and `NoopProvider` fallback.

**2026 research:** sentence-transformers v5.3.0 is the latest (March 2026). It remains the dominant library for local embedding generation — no competitor has overtaken it. The project is actively maintained. Our code uses only stable APIs (`SentenceTransformer()`, `.encode()`, `.get_sentence_embedding_dimension()`). No deprecation concerns.

**Action:** Since it's already a required dependency, remove all conditional import logic:
- Remove the feature-flag check in `embeddings.py:27-35`
- Import `SentenceTransformer` directly at module level
- Remove `NoopProvider` class (keep only for test mocks if needed, but don't use in production paths)
- Remove `TAPPS_SEMANTIC_SEARCH` env var opt-out (see §7)
- Simplify `get_embedding_provider()` — always return `SentenceTransformerProvider`

### 4b. `sqlite-vec` — already core, formalize it

Currently: Core dependency, but `sqlite_vec_index.py` has `try_load_extension()` that returns False gracefully.

**2026 research:** sqlite-vec v0.1.9 is latest (March 2026). Still pre-1.0 but actively maintained (resumed strong development March 2026). Remains the best SQLite vector search option. Brute-force KNN is performant at <100K vectors. IVF/DiskANN indexes coming in v0.1.10 for larger scale.

**Action:** Make the extension load a hard requirement:
- If `sqlite_vec.load()` fails, raise an error instead of returning False
- Remove graceful degradation paths for missing sqlite-vec
- Bump lower bound to `>=0.1.9` (fixes a DELETE bug with metadata text columns >12 chars)

### 4c. `typer` — keep as optional extra

**2026 research reversal:** Typer is at v0.24.1 (Feb 2026), still no 1.0. It's the right CLI framework — Cyclopts is the only competitor and doesn't justify a migration for 43 commands.

However, research on Python packaging best practices confirms: **the core library is useful without the CLI**. Users embedding tapps-brain as a Python library don't need typer. The `[project.scripts]` entry point pattern with a graceful error message is the standard approach.

**Action:** Keep `typer` as `[cli]` extra. Ensure the `tapps-brain` console script prints a helpful error ("Install with `pip install tapps-brain[cli]`") if typer is missing. Remove lazy import complexity — use a simple try/except at the entry point only.

### 4d. `mcp` — keep as optional extra

**2026 research reversal:** MCP SDK is at v1.27.0 (April 2026). FastMCP remains the correct approach.

Same logic as typer: **the core library is useful without the MCP server**. MCP adds its own dependency tree. Users who only want the Python API should not be forced to install it. The `[all]` extra covers users who want everything.

**Important MCP finding:** With 40+ tools, context window consumption is 20,000-40,000+ tokens before the user asks anything. Consider tool grouping/filtering in a future release to manage this.

**Action:** Keep `mcp` as `[mcp]` extra. Tighten version pin to `>=1.25.0,<2` (v1.25 is the maintenance branch point; gets lazy imports, spec alignment). Ensure `tapps-brain-mcp` prints a helpful error if the extra is missing.

### 4e. `opentelemetry-api` — make core (lightweight, no-op safe)

**2026 research (new finding):** The OTel API package is specifically designed for libraries. It is:
- A no-op by default — if no SDK is installed, all tracing/metrics calls silently do nothing
- Lightweight (~100KB, minimal transitive deps)
- The official guidance: "Libraries MUST depend only on `opentelemetry-api`, never on `opentelemetry-sdk`"

This means we can make `opentelemetry-api` a core dependency and **remove all try/except guards** around OTel instrumentation code. Users who want actual telemetry export install `opentelemetry-sdk` separately (or via `[otel]` extra).

**Action:**
- Move `opentelemetry-api>=1.20,<2` to core `dependencies` (tighten upper bound from `<3` to `<2` — v2 has no timeline and would imply breaking changes)
- Keep `opentelemetry-sdk>=1.20,<2` in `[otel]` optional extra
- Remove the `otel` feature flag — the API is always available
- Remove all `try/except ImportError` guards for `opentelemetry.metrics`, `opentelemetry.trace` etc.
- Use `opentelemetry.trace.get_tracer(__name__)` directly (returns no-op tracer if no SDK)

---

## 5. Consolidate Dual/Triple Options

### 5a. Relevance normalization: pick minmax, remove sigmoid

**2026 research correction:** Our original recommendation was wrong. Updated findings:

- **Min-max normalization** is the industry standard for hybrid search score normalization. It is the default in OpenSearch and most production systems.
- **Sigmoid normalization** produced the lowest prediction accuracy compared to other methods in comparative research and is **not recommended** for hybrid search.
- **Z-score normalization** showed a modest 2.08% improvement over min-max (OpenSearch benchmarks, June 2025) but adds complexity.
- **However:** If using RRF (which operates on ranks, not scores), normalization is largely moot — RRF inherently sidesteps score scale differences.

**Decision:** Since we use RRF for fusion, neither normalization matters much for the hybrid path. For the non-hybrid (BM25-only) path, min-max is the better standard. But the simplest correct action is:

**Action:**
- Remove `relevance_normalization` field from `ScoringConfig`
- Keep **min-max** as the single normalization for the BM25-only (non-hybrid) path
- Remove sigmoid normalization code
- For the hybrid RRF path, normalization is moot (RRF uses ranks) — simplify accordingly

### 5b. Decay models: keep both exponential and power_law

Both are legitimate for different use cases (exponential for most tiers, power_law for long-tail research). These are **not redundant** — they model genuinely different decay curves.

**Action:** Keep both. No change needed.

### 5c. Hive conflict policies: keep all 4

All four policies (`supersede`, `source_authority`, `confidence_max`, `last_write_wins`) are fully implemented, tested, and serve distinct multi-agent scenarios.

**Action:** Keep all. No change needed.

### 5d. Embedding providers: eliminate the provider abstraction

With `sentence-transformers` mandatory (§4a), there's only one real provider. The `EmbeddingProvider` protocol and `NoopProvider` exist only to handle the "not installed" case.

**Action:**
- Remove the `EmbeddingProvider` protocol
- Remove `NoopProvider`
- Inline `SentenceTransformerProvider` logic directly into `embeddings.py`
- The module exports a simple `embed(text) -> list[float]` and `embed_batch(texts) -> list[list[float]]` interface

### 5e. Reranker: replace Cohere-only with local default + Cohere option

See dedicated §11 below — this is a significant upgrade, not just a simplification.

### 5f. BM25: evaluate replacing custom implementation with `bm25s`

**2026 research (new finding):** The `bm25s` library (also installable as `pip install bm25`) achieves up to 500x speedup over `rank-bm25` using sparse matrix optimizations, with only NumPy as a dependency (already in our stack). It supports index persistence (save/load).

**Evaluate:** Compare our pure-Python `bm25.py` (192 lines) against `bm25s` for:
- Correctness on our test suite
- Performance at our scale (<100K entries)
- API simplicity

**Action:** If `bm25s` passes evaluation, replace `bm25.py` with a thin wrapper around `bm25s`. Add `bm25s` to core dependencies. If our custom implementation is "good enough" at our scale, keep it — the 500x speedup matters less at <100K entries.

---

## 6. Schema Migration Collapse

Currently: 17 sequential migration steps from v1 to v17, each an individual method in `persistence.py`.

Since this is the first production release, **no database exists in the wild at any prior schema version**.

**2026 research confirms:** For a first release, shipping a single schema creation script is the accepted best practice. Use `PRAGMA user_version` to stamp it. Add incremental migrations only for subsequent releases.

**Action:**
- Remove all 16 migration methods (`_migrate_v1_to_v2` through `_migrate_v16_to_v17`)
- Remove the `_migration_steps` list
- Remove `_SCHEMA_V2` through `_SCHEMA_V16` constants
- Replace the migration loop with a single `CREATE TABLE` that defines the final schema directly
- **Reset to `_SCHEMA_VERSION = 1`** — this is v1 of the production schema
- Use `PRAGMA user_version = 1` to stamp new databases
- This removes ~400 lines from `persistence.py`

---

## 7. Environment Variable Cleanup

### Current state: 6 TAPPS_* env vars

| Env Var | Keep? | Rationale |
|---------|-------|-----------|
| `TAPPS_BRAIN_ENCRYPTION_KEY` | **Yes** | Necessary for SQLCipher — no better alternative |
| `TAPPS_BRAIN_HIVE_ENCRYPTION_KEY` | **Yes** | Separate hive key is a valid security need |
| `TAPPS_SQLITE_BUSY_MS` | **Yes** | Useful operational tuning. **2026 research:** 5000ms default is correct; 10-20s common in production. Current max of 3,600,000ms is fine. |
| `TAPPS_SQLITE_MEMORY_READONLY_SEARCH` | **Keep** | Enables separate read-only connection for FTS/vec queries. Useful for heavy concurrent read loads. Make it documented, not default. |
| `TAPPS_STORE_LOCK_TIMEOUT_S` | **Yes** | Useful operational tuning knob |
| `TAPPS_SEMANTIC_SEARCH` | **Remove** | With sentence-transformers mandatory, this opt-out defeats the purpose |

**Action:** Remove `TAPPS_SEMANTIC_SEARCH`. Keep the other 5.

---

## 8. Optional Dependency Simplification

### After changes from §4, the dependency picture becomes:

**Core (always installed):**
- pydantic >=2.12.5,<3
- structlog >=25.5.0,<26
- pyyaml >=6.0.3,<7
- numpy >=2.4.2,<3
- sentence-transformers >=5.2.3,<6
- sqlite-vec >=0.1.9,<0.2 *(bumped lower bound)*
- opentelemetry-api >=1.20,<2 *(moved from otel extra, tightened upper bound)*

**Remaining optional extras:**
- `[cli]` — typer >=0.12.3,<1
- `[mcp]` — mcp >=1.25.0,<2 *(tightened lower bound)*
- `[reranker]` — cohere >=5.0,<6 (Cohere API reranking)
- `[local-reranker]` — flashrank >=0.2.9 (local reranking, see §11)
- `[encryption]` — pysqlcipher3 >=1.2.0,<2
- `[otel]` — opentelemetry-sdk >=1.20,<2 *(only SDK now, API is core)*
- `[all]` — all of the above

**Removed extras:**
- `[faiss]` — dead code, removed entirely

---

## 9. Dependency Version Pin Updates

**2026 research validated all pins. Recommended updates:**

| Dependency | Current Pin | Recommended Pin | Rationale |
|-----------|-------------|-----------------|-----------|
| pydantic | >=2.12.5,<3 | **>=2.12.5,<3** | Correct. v3 not released, will remove deprecated APIs. Keep the <3 guard. |
| structlog | >=25.5.0,<26 | **>=25.5.0,<26** | Correct. v25.5.0 is latest. No 2026 releases yet. |
| pyyaml | >=6.0.3,<7 | **>=6.0.3,<7** | Correct. Stable, no changes needed. |
| numpy | >=2.4.2,<3 | **>=2.4.2,<3** | Correct. Required by sentence-transformers. |
| sentence-transformers | >=5.2.3,<6 | **>=5.2.3,<6** | Correct. v5.3.0 is latest. No deprecated APIs used. |
| sqlite-vec | >=0.1.6,<0.2 | **>=0.1.9,<0.2** | Bump floor to get DELETE bug fix (v0.1.9, March 2026). |
| typer | >=0.12.3,<1 | **>=0.15.0,<1** | Consider bumping floor. v0.14 had breaking change (auto-naming removed). v0.22+ requires Rich always. |
| mcp | >=1.2.0,<2 | **>=1.25.0,<2** | Bump floor. v1.25 is the v1.x maintenance branch point with lazy imports, spec 2025-11-25 alignment. |
| cohere | >=5.0,<6 | **>=5.0,<6** | Correct. v5.21.1 is latest. |
| opentelemetry-api | >=1.20,<3 | **>=1.20,<2** | Tighten upper bound. OTel has no v2 plans; <2 is the standard convention. |
| opentelemetry-sdk | >=1.20,<3 | **>=1.20,<2** | Same as API. |

### Pydantic notes for future reference
- v2.12.5 is the latest stable (Nov 2025); v2.13.0b3 is in beta (March 2026)
- v3 is imminent but NOT released — will primarily remove v1 shims and deprecated methods
- Our code is clean: uses `ConfigDict`, `field_validator`, `model_validator`, no v1 compat layer
- **Watch for v2.13 stable:** adds `polymorphic_serialization` option
- **Performance tip:** Use `model_construct()` in hot paths where data is already validated

### structlog notes
- `pad_event` renamed to `pad_event_to` in v25.5.0 — check if we use `ConsoleRenderer`
- `TimeStamper` now returns timezone-aware datetimes (v25.2.0) — verify downstream parsing

---

## 10. Embedding Model Upgrade

**2026 research (critical finding):** `all-MiniLM-L6-v2` is widely considered **outdated for new projects**.

| Model | Params | MTEB Score | Context | Dims |
|-------|--------|-----------|---------|------|
| all-MiniLM-L6-v2 (current) | 22M | 56.3 | 512 | 384 |
| **BAAI/bge-small-en-v1.5** (recommended) | 33M | ~62 | 512 | 384 |
| nomic-ai/nomic-embed-text-v1.5 | 137M | ~65 | 8192 | 768 |
| intfloat/e5-small-v2 | 33M | ~60 | 512 | 384 |

**Recommendation:** Switch default from `all-MiniLM-L6-v2` to **`BAAI/bge-small-en-v1.5`**:
- Near drop-in replacement: same 384 dimensions, same 512 context window
- ~10% better retrieval quality (MTEB 62 vs 56.3)
- Similar inference speed
- Since we track `embedding_model_id` per row (STORY-042.2), old rows retain provenance and can be reindexed

**Action:**
- Change default model constant in `embeddings.py` from `"all-MiniLM-L6-v2"` to `"BAAI/bge-small-en-v1.5"`
- Update docs and embedding model card
- Add a CLI command or note about reindexing existing entries (for users upgrading from pre-release)

---

## 11. Reranker: Add Local Default

**2026 research (major finding):** tapps-brain bills itself as "zero LLM dependency" but the only reranker option is Cohere (external API). This contradicts the principle and adds privacy concerns (memory snippets sent to Cohere's API).

### Recommended: FlashRank as default local reranker

**FlashRank** (v0.2.9):
- No PyTorch or Transformers dependency required
- Smallest model: `ms-marco-TinyBERT-L-2-v2` (~4MB, runs on CPU)
- Best quality model: `ms-marco-MiniLM-L-12-v2` (~34MB)
- Dead simple API: `Ranker()` + `RerankRequest(query, passages)` + `ranker.rerank(request)`
- Zero heavy dependencies, extremely fast on CPU
- Aligns with "zero LLM dependency" / "fully deterministic" philosophy

### Alternative considered: sentence-transformers CrossEncoder
- `cross-encoder/ms-marco-MiniLM-L-6-v2` — zero new deps (sentence-transformers already core)
- Good quality, but uses the heavy PyTorch inference path
- FlashRank is lighter and faster for pure reranking

### New reranker architecture

```
[reranker]     → cohere >=5.0,<6          (API-based, optional)
[local-reranker] → flashrank >=0.2.9      (local, optional, recommended)
```

**Action:**
- Add `FlashRankReranker` implementing the existing `Reranker` protocol
- Add `[local-reranker]` extra with `flashrank>=0.2.9`
- Update `get_reranker()` factory: `provider="flashrank"` (new default when installed), `provider="cohere"` (existing)
- Keep `NoopReranker` for when no reranker is configured
- Remove Cohere v1/v2 `hasattr` shim (use `ClientV2` directly)
- Update docs to recommend FlashRank as the default reranker

---

## 12. SQLite Best Practices

**2026 research surfaced several important findings:**

### 12a. WAL mode — confirmed correct

WAL mode remains the standard. WAL2 is experimental, not in any official release, and should NOT be used.

Recommended production pragmas (verify we set all of these):
```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;    -- safe for WAL; only loses durability on power loss
PRAGMA busy_timeout = 5000;     -- our TAPPS_SQLITE_BUSY_MS default
PRAGMA foreign_keys = ON;
PRAGMA wal_autocheckpoint = 1000; -- default, good starting point
```

**Action:** Audit `persistence.py` and `sqlcipher_util.py` to ensure all these pragmas are set. Add any missing ones.

### 12b. SQLite version — WAL corruption bug

A WAL-reset bug affecting SQLite 3.7.0 through 3.51.2 was fixed in **SQLite 3.51.3** (March 2026). This can cause database corruption in rare cases.

**Action:** Document minimum recommended SQLite version as 3.51.3+. Consider adding a startup warning if the bundled SQLite version is below 3.51.3.

### 12c. Consider APSW for future versions

APSW (Another Python SQLite Wrapper) offers:
- `bestpractice.apply()` auto-configures WAL, busy timeout, foreign keys, etc.
- Cross-thread connection sharing (simplifies async)
- Full SQLite API access (VFS, blob I/O, backups)
- `apsw-sqlite3mc` bundles APSW + encryption (SQLCipher-compatible + ChaCha20-Poly1305)

**Action:** Not for v1 (stdlib sqlite3 works fine and avoids a new dependency), but file as a future consideration. If encryption support grows in importance, `apsw-sqlite3mc` would replace both `sqlite3` and `pysqlcipher3`.

### 12d. FTS5 optimization

**2026 best practices for FTS5:**
- Use `porter unicode61` tokenizer for English text with stemming
- Use external content tables (`content=`) to avoid data duplication
- Periodically run `INSERT INTO fts_table(fts_table) VALUES('optimize')` to merge index segments

**Action:** Audit FTS5 setup in `persistence.py`. Ensure we use `porter unicode61` tokenizer. Add an `optimize_fts()` method to the maintenance CLI.

---

## 13. Interface Boilerplate Reduction

### 13a. MCP server tool definitions (~2451 lines)

`mcp_server.py` is the largest file with repetitive tool definitions. Each of the 40+ tools follows the same pattern: parse args, validate, call store method, format response, handle errors.

**2026 MCP research note:** Each MCP tool adds ~500-1,000 tokens to context windows. With 40+ tools, that's 20,000-40,000+ tokens consumed before the user asks anything. Consider tool grouping/filtering in a future release.

**Recommendation:** Create a lightweight tool decorator/factory that reduces per-tool boilerplate from ~50 lines to ~15 lines. This is a refactoring task, not a feature change.

**Estimated savings:** ~800 lines

### 13b. CLI command definitions (~3369 lines)

**2026 Typer best practices for large apps:**
- One module per command group (`commands/deploy.py`, etc.)
- Lazy imports inside command functions (cuts startup from 400ms+ to <150ms)
- Shared utilities in `_common.py` — callbacks, `--format json`, error handling
- Use `typer.Context` for shared state via group callbacks

**Recommendation:** Extract common patterns (store opening, error handling, JSON output formatting) into shared decorators. Consider splitting `cli.py` into per-group modules.

**Estimated savings:** ~500 lines

### 13c. MCP transport — add Streamable HTTP support (future)

**2026 research:** SSE transport is deprecated in MCP spec. Streamable HTTP is the modern standard for remote servers. Stdio remains correct for local use.

**Action (future):** Add Streamable HTTP transport for team/remote deployments. Not a v1 blocker — stdio is correct for the primary use case (local coding assistants).

---

## 14. Adopt PEP 735 Dependency Groups

**2026 research (new standard):** PEP 735 (accepted October 2024) adds `[dependency-groups]` to pyproject.toml for dev/test/lint/docs dependencies. Unlike `[project.optional-dependencies]`, dependency groups are **never published** to PyPI metadata.

Supported by: pip 25.1+ (`pip install --group test`), uv, Poetry (adding support).

**Current state:** Dev deps are in `[project.optional-dependencies] dev = [...]` — this means dev deps leak into published package metadata on PyPI.

**Action:** Move dev dependencies to `[dependency-groups]`:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0,<9",
    "pytest-asyncio>=0.25,<1",
    "pytest-cov>=6.0,<7",
    "pytest-benchmark>=5.0,<6",
    "mypy>=1.14,<2",
    "ruff>=0.9,<1",
    "mcp>=1.25.0,<2",
    "typer>=0.15.0,<1",
]
```

Remove the `dev` extra from `[project.optional-dependencies]`.

---

## 15. Estimated Impact

### Lines removed/simplified

| Category | Estimated Lines Removed |
|----------|------------------------|
| Dead modules (rake, textrank, pagerank, louvain) | ~450 |
| Dead tests for removed modules | ~400 |
| migration.py + tests | ~500 |
| profile_migrate.py + tests | ~200 |
| encryption_migrate.py + tests | ~150 |
| Schema migration methods (persistence.py) | ~400 |
| Feature flags cleanup | ~50 |
| Legacy tier/scope/decay compat code | ~175 |
| Embedding provider abstraction simplification | ~80 |
| Relevance normalization (sigmoid removal) | ~30 |
| Lazy import / graceful degradation for now-core deps | ~60 |
| FTS5 LIKE fallbacks | ~40 |
| OTel try/except guards (now core API) | ~30 |
| **Subtotal: code removal** | **~2,565** |
| MCP/CLI boilerplate reduction (refactor) | ~1,300 |
| **Total impact** | **~3,865 lines** (~13% of codebase) |

### Lines added

| Category | Estimated Lines Added |
|----------|----------------------|
| FlashRankReranker implementation | ~60 |
| Single CREATE TABLE schema | ~50 (net reduction after removing migrations) |
| SQLite pragma audit additions | ~10 |
| Helpful error messages for optional deps | ~20 |
| **Total additions** | **~140** |

### Net reduction: ~3,725 lines (~12.4% of codebase)

### Files deleted

| File | Lines |
|------|-------|
| `src/tapps_brain/rake.py` | 87 |
| `src/tapps_brain/textrank.py` | 185 |
| `src/tapps_brain/pagerank.py` | 71 |
| `src/tapps_brain/louvain.py` | 106 |
| `src/tapps_brain/migration.py` | 359 |
| `src/tapps_brain/profile_migrate.py` | 110 |
| `src/tapps_brain/encryption_migrate.py` | 82 |
| + corresponding test files | ~600 |
| **Total files deleted:** 7 source + ~7 test | **~1,600 lines** |

### Config simplification

| Before | After |
|--------|-------|
| 8 feature flags | 2 feature flags |
| 6 optional extras + 1 dev extra | 6 optional extras + PEP 735 dev group |
| 6 env vars | 5 env vars |
| 17 schema versions | 1 schema version |
| 2 relevance normalization modes | 1 (minmax) |
| 2 embedding providers (Noop + real) | 1 (SentenceTransformer) |
| 1 reranker backend (Cohere only) | 2 backends (FlashRank local + Cohere API) |
| Legacy + profile tier validation | Profile-only tier validation |
| Default model: all-MiniLM-L6-v2 | Default model: BAAI/bge-small-en-v1.5 |

---

## 16. Implementation Order

Recommended sequence to minimize merge conflicts:

### Phase 1 — Dead code removal (low risk, high confidence)
- Delete 4 unused algorithm modules + tests
- Delete migration.py, profile_migrate.py, encryption_migrate.py + tests
- Remove FAISS from pyproject.toml and feature flags

### Phase 2 — Dependency updates (low risk)
- Bump version pins (sqlite-vec >=0.1.9, mcp >=1.25.0, otel <2)
- Move opentelemetry-api to core dependencies
- Adopt PEP 735 for dev dependencies
- Remove OTel feature flag and try/except guards

### Phase 3 — Make core deps formal (medium risk)
- Remove lazy imports for sentence-transformers, sqlite-vec
- Remove TAPPS_SEMANTIC_SEARCH env var
- Simplify embedding provider (remove protocol + NoopProvider)
- Clean up feature flags to 2 (anthropic_sdk, openai_sdk)

### Phase 4 — Remove backwards compat (medium risk)
- Remove legacy tier enum union logic
- Remove decay config legacy field mapping
- Remove Pydantic AliasChoices (rename to top_k_lexical/top_k_dense)
- Remove memory relay scope migration
- Remove Cohere v1/v2 shim
- Remove FTS5 LIKE fallbacks
- Replace unknown tier/source fallbacks with errors

### Phase 5 — Schema collapse (higher risk, requires careful testing)
- Collapse 17 migrations into single CREATE TABLE
- Reset schema version to 1
- Remove all version guards in persistence.py and store.py
- Audit SQLite pragmas (WAL, synchronous, foreign_keys)

### Phase 6 — Consolidate options (medium risk)
- Remove sigmoid relevance normalization, keep minmax
- Switch default embedding model to BAAI/bge-small-en-v1.5
- Add FlashRankReranker as local reranker option

### Phase 7 — Boilerplate reduction (refactor, can be done incrementally)
- MCP server tool factory
- CLI command decorators / module split
- CLI lazy imports for startup performance

---

## Research Sources (April 2026)

All recommendations validated against current ecosystem state:

- **Pydantic:** v2.12.5 stable, v2.13.0b3 beta, v3 not released
- **sentence-transformers:** v5.3.0 (March 2026), all-MiniLM-L6-v2 widely considered outdated
- **sqlite-vec:** v0.1.9 (March 2026), pre-1.0, active development resumed
- **MCP SDK:** v1.27.0 (April 2026), v1.25 = maintenance branch, v2 no firm timeline
- **Cohere:** v5.21.1, ClientV2 is standard, rerank-v3.5 is latest model
- **FlashRank:** v0.2.9, no-PyTorch local reranking, 4MB smallest model
- **structlog:** v25.5.0 (Oct 2025), no 2026 releases yet, remains the standard
- **SQLite:** WAL-reset bug fixed in 3.51.3 (March 2026), WAL2 still experimental
- **Typer:** v0.24.1 (Feb 2026), still no 1.0, Cyclopts is only competitor
- **OpenTelemetry:** v1.40.0 (March 2026), API designed as core lib dep, SDK stays optional
- **Python packaging:** PEP 735 dependency groups accepted, uv is dominant, hatchling correct for this project
- **Hybrid search:** RRF remains best fusion default, BM25+vector is standard even at <100K
- **BAAI/bge-small-en-v1.5:** MTEB ~62, drop-in 384-dim replacement for MiniLM
