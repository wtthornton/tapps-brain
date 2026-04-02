---
id: EPIC-049
title: "Dependency extras and supply chain — research and upgrades"
status: planned
priority: medium
created: 2026-03-31
tags: [packaging, dependencies, security, vector, encryption]
---

# EPIC-049: Dependency extras (install surface)

## Context

Maps to **§8** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md) and `pyproject.toml` optional-dependencies.

## Success criteria

- [ ] `uv sync` / pip extras documented with **compat matrix** (Python version × platform × optional native libs).

## Stories

**§8 / `pyproject.toml` order:** **049.1** `cli` → **049.2** `mcp` → **049.3** `vector` → **049.4** `reranker` → **049.5** `encryption` → **049.6** `otel` → **049.7** core deps (pydantic, structlog, pyyaml).

### STORY-049.1: `cli` extra (Typer)

**Status:** planned | **Effort:** S | **Depends on:** none  
**Context refs:** `pyproject.toml`, `src/tapps_brain/cli.py`, `tests/unit/test_cli.py`  
**Verification:** `pytest -m "requires_cli and not benchmark" -v --tb=short`

#### Implementation themes

- [ ] **Minimal install** path documented: library-only without Typer.
- [ ] Version pin rationale in **CHANGELOG** when Typer major bumps.

---

### STORY-049.2: `mcp` extra

**Status:** planned | **Effort:** S | **Depends on:** none  
**Context refs:** `pyproject.toml`, `src/tapps_brain/mcp_server.py`, `tests/unit/test_mcp_server.py`  
**Verification:** `pytest -m "requires_mcp and not benchmark" -v --tb=short`

#### Implementation themes

- [ ] Track **MCP SDK** breaking changes with compat table.
- [ ] **SSE vs stdio** transport doc for hosts.

---

### STORY-049.3: `vector` extra (FAISS, numpy, sentence-transformers, sqlite-vec)

**Status:** planned | **Effort:** L | **Depends on:** none  
**Context refs:** `pyproject.toml`, `src/tapps_brain/_feature_flags.py`, `src/tapps_brain/embeddings.py`, `tests/unit/test_memory_embeddings.py`, `tests/unit/test_sqlite_vec_index.py`, `tests/unit/test_persistence_sqlite_vec.py`, `tests/unit/test_sqlite_vec_try_load.py`  
**Verification:** `pytest tests/unit/test_memory_embeddings.py tests/unit/test_sqlite_vec_index.py tests/unit/test_persistence_sqlite_vec.py tests/unit/test_sqlite_vec_try_load.py -v --tb=short -m "not benchmark"` (skip or narrow if optional native/`[vector]` deps absent locally; document CI matrix separately)

#### Research notes (2026-forward)

- **faiss-cpu** vs **GPU** wheel availability — document **non-PyPI** install for GPU.
- **numpy 2** ABI compatibility with pinned ST versions — watch release notes.

#### Implementation themes

- [ ] **Split** extra: `vector-embed` vs `vector-index` if users want ST without sqlite-vec native build.
- [ ] **Dockerfile** recipe for reproducible vector images.

---

### STORY-049.4: `reranker` extra (Cohere)

**Status:** planned | **Effort:** S | **Depends on:** none  
**Context refs:** `src/tapps_brain/reranker.py`, `pyproject.toml`, `tests/unit/test_reranker.py`  
**Verification:** `pytest tests/unit/test_reranker.py -v --tb=short -m "not benchmark"`

#### Implementation themes

- [ ] **API key rotation** and **timeout** env knobs documented.

---

### STORY-049.5: `encryption` extra (pysqlcipher3 / SQLCipher)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `pyproject.toml`, `src/tapps_brain/sqlcipher_util.py`, `tests/unit/test_sqlcipher_util.py`, `tests/unit/test_sqlcipher_wiring.py`  
**Verification:** `pytest -m "requires_encryption and not benchmark" -v --tb=short`

#### Implementation themes

- [ ] **Platform matrix**: macOS/Homebrew, Ubuntu, Windows build pain points.
- [ ] **Pure fallback** messaging when extra missing but key set.

---

### STORY-049.6: `otel` extra

**Status:** planned | **Effort:** S | **Depends on:** none  
**Context refs:** `src/tapps_brain/otel_exporter.py`, `pyproject.toml`, `tests/unit/test_otel_exporter.py`  
**Verification:** `pytest tests/unit/test_otel_exporter.py -v --tb=short -m "not benchmark"` (optional manual span export checklist in observability guide)

#### Implementation themes

- [ ] Pin **OTel** minor range policy; **exporter** plugin split if needed.

---

### STORY-049.7: Core dependencies + supply chain (pydantic, structlog, pyyaml)

**Status:** planned | **Effort:** M | **Depends on:** none  
**Context refs:** `pyproject.toml`, `uv.lock` (if present), Dependabot policy if any, `tests/` (full suite)  
**Verification:** `pytest tests/ -v --tb=short -m "not benchmark"` (full suite on dependency bump PRs; use CI matrix when optional extras are skipped locally)

#### Research notes (2026-forward)

- **SBOM** export (`cyclonedx-bom`) for enterprise consumers.
- **`uv lock`** or **pip-tools** lockfile commitment for reproducible builds.

#### Implementation themes

- [ ] Generate **SBOM** in release workflow (optional artifact).
- [ ] **Security** policy: how fast to bump CVE-flagged deps.

## Priority order

**049.7**, **049.3** → **049.5** → **049.2**, **049.1**, **049.4**, **049.6**.
