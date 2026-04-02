---
id: EPIC-043
title: "Storage, persistence, and schema — research and upgrades"
status: planned
priority: high
created: 2026-03-31
tags: [sqlite, persistence, fts5, pydantic, encryption, audit]
---

# EPIC-043: Storage, persistence, and schema

## Context

Maps to **§2** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). Ground truth: `persistence.py`, `data-stores-and-schema.md`.

## Success criteria

- [ ] Schema changes remain **migratable** from v17 with tests.
- [ ] Security-sensitive paths (encryption, audit) have **operator docs** updates when behavior changes.

## Stories

**§2 table order:** **043.1** embedded SQLite/WAL → **043.2** versioned migrations → **043.3** FTS5+triggers → **043.4** Pydantic → **043.5** structlog → **043.6** SQLCipher → **043.7** JSONL audit.

### STORY-043.1: Embedded OLTP (SQLite + WAL)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/persistence.py`, `src/tapps_brain/hive.py`, `src/tapps_brain/federation.py`, `tests/unit/test_memory_persistence.py`  
**Verification:** `pytest tests/unit/test_memory_persistence.py -v --tb=short -m "not benchmark"`

#### Code baseline

Three DBs: project `memory.db`, Hive, federation hub; WAL where configured.

#### Research notes (2026-forward)

- **busy_timeout**, **cache_size**, **mmap_size** pragmas often recommended for production SQLite; evaluate **read vs write** contention under MCP burst (see EPIC-050).
- **Single-writer** remains fundamental; queue or **write serializer** pattern if lock errors surface.

#### Implementation themes

- [ ] Audit **PRAGMA** set on open; document in engineering doc.
- [ ] Metrics: **sqlite busy** / lock wait (if observable).
- [ ] Spike: read-only **connection** for heavy search paths vs single shared connection (if safe with our lock model).

---

### STORY-043.2: Versioned schema migrations (`_ensure_schema`)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `src/tapps_brain/persistence.py` `_ensure_schema`, `docs/engineering/data-stores-and-schema.md`, `tests/unit/test_memory_persistence.py`  
**Verification:** `pytest tests/unit/test_memory_persistence.py -k migration -v --tb=short -m "not benchmark"`

#### Code baseline

**v17** includes `embedding_model_id`, `memory_group`, temporal columns, embeddings, etc. Migrations are **imperative SQL in Python** (version steps), not a separate declarative migration DSL — the feature map uses “declarative” in the product sense of **declared schema versions**, not Flyway-style files.

#### Research notes (2026-forward)

- Prefer **additive** migrations + **backfill** jobs over destructive steps for shipped users.
- **Expand-contract** pattern if external readers exist (future service mode).

#### Implementation themes

- [ ] Migration **dry-run** or `EXPLAIN` logging in debug mode.
- [ ] Automated test: **fresh DB** + **upgrade from fixture vN** snapshot.

---

### STORY-043.3: Full-text index (FTS5 + triggers)

**Status:** planned | **Effort:** S | **Depends on:** none  
**Context refs:** `src/tapps_brain/persistence.py`, `src/tapps_brain/hive.py`, `src/tapps_brain/federation.py` (FTS definitions), `tests/unit/test_memory_persistence.py`, `tests/unit/test_hive_memory_group.py`, `tests/unit/test_federation.py`  
**Verification:** `pytest tests/unit/test_memory_persistence.py tests/unit/test_hive_memory_group.py tests/unit/test_federation.py -v --tb=short -m "not benchmark"`

#### Code baseline

Synced triggers maintain `memories_fts`, session FTS, Hive, federation FTS.

#### Research notes (2026-forward)

- **FTS5** tokenizers: `unicode61` vs `porter` — affects recall for technical text; **deterministic** choice must be documented.
- **External content** FTS tables require trigger discipline — audit **failure** modes if base row missing.

#### Implementation themes

- [ ] Document tokenizer + **rebuild** command if tokenizer ever changes (major version bump).
- [ ] Consistency test: insert → immediate FTS search finds row.

---

### STORY-043.4: Structured config / validation (Pydantic v2)

**Status:** planned | **Effort:** S | **Depends on:** none  
**Context refs:** `src/tapps_brain/models.py`, `src/tapps_brain/profile.py`, `tests/unit/test_memory_models.py`, `tests/unit/test_profile.py`  
**Verification:** `pytest tests/unit/test_memory_models.py tests/unit/test_profile.py -v --tb=short -m "not benchmark"`

#### Code baseline

Pydantic v2 models for entries and profiles.

#### Research notes (2026-forward)

- Stay on **supported** pydantic 2.x; watch **computed_field** / **JSON schema** generation for MCP tool manifests.
- **Validation performance** on bulk import — optional `model_construct` in trusted paths only with audit.

#### Implementation themes

- [ ] Pin **upper bound** policy in `pyproject.toml` with rationale.
- [ ] Generate **JSON Schema** for profile subset for editor tooling (optional).

---

### STORY-043.5: Structured logging (structlog)

**Status:** planned | **Effort:** S | **Depends on:** none  
**Context refs:** logger usage across `src/tapps_brain/store.py`, `src/tapps_brain/mcp_server.py`, `tests/unit/test_mcp_server.py`  
**Verification:** `uv run ruff check src/ tests/ && uv run mypy --strict src/tapps_brain/` (static gate; no dedicated structlog tests); optional manual: one structured log sample in operator doc when changing events

#### Code baseline

structlog for structured fields.

#### Research notes (2026-forward)

- Align with **OpenTelemetry** log correlation (trace_id) when OTel enabled (EPIC-047).
- **PII redaction** middleware for `value` fields in debug logs — policy.

#### Implementation themes

- [ ] Standardize **event names** for save/recall/error (`memory_save_*`, `recall_*`).
- [ ] Document **log levels** for operators.

---

### STORY-043.6: Encryption at rest (SQLCipher)

**Status:** planned | **Effort:** L | **Depends on:** none  
**Context refs:** `src/tapps_brain/sqlcipher_util.py`, `docs/guides/sqlcipher.md`, CLI maintenance commands, `tests/unit/test_sqlcipher_util.py`, `tests/unit/test_sqlcipher_wiring.py`, `tests/unit/test_encryption_migrate.py`  
**Verification:** `pytest -m "requires_encryption and not benchmark" -v --tb=short`

#### Code baseline

`pysqlcipher3` extra; key from env/config; multi-store support.

#### Research notes (2026-forward)

- **Key rotation** and **backup** (encrypted blob portability) are operator-critical.
- **KDF** iterations and **cipher** defaults should track SQLCipher recommendations.

#### Implementation themes

- [ ] Runbook: **lost key = data loss** prominently.
- [ ] Spike: **HSM/KMS** integration story (out of process key) for enterprise.
- [ ] Automated test matrix note: CI may skip without native SQLCipher.

---

### STORY-043.7: Append-only audit (JSONL)

**Status:** planned | **Effort:** S | **Depends on:** none  
**Context refs:** `src/tapps_brain/persistence.py` (append path / `memory_log.jsonl`), `src/tapps_brain/audit.py` (`AuditReader` query API), `tests/unit/test_audit.py`  
**Verification:** `pytest tests/unit/test_audit.py -v --tb=short -m "not benchmark"`

#### Code baseline

JSONL audit file under project store dir; **writes** originate from persistence layer; **reads/filters** use `audit.py`.

#### Research notes (2026-forward)

- **Tamper evidence:** hash chain or signed segments for compliance asks (optional epic).
- **Rotation / retention** policy to avoid unbounded disk.

#### Implementation themes

- [ ] Document **fields** and **rotation** recommendation.
- [ ] Optional: **async batch** writer to reduce fsync hot spots (careful with durability).

## Priority order

**043.2** (migrations) and **043.1** (SQLite pragmas/WAL) first — wrong migrations break everything. Then **043.6** (encryption ops), **043.3** (FTS), **043.7** (audit), **043.4** (Pydantic), **043.5** (logging/OTel correlation).
