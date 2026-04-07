# ADR-001: Retrieval stack — embedded SQLite-first (defer learned sparse, ColBERT, managed vector DB)

**Status:** Accepted  
**Date:** 2026-04-03  
**Owner:** @wtthornton  
**Epic / story:** [EPIC-051](../epics/EPIC-051.md) — STORY-051.1  
**Context:** [features-and-technologies.md](../../engineering/features-and-technologies.md) section 10 checklist item 1

## Context

The improvement program asked whether the shipped retrieval path should remain **lexical + optional dense + hybrid fusion**, or expand toward **learned sparse** encoders (e.g. SPLADE-style), **ColBERT-style** late interaction, or a **managed external vector database** (e.g. pgvector, Milvus) as a first-class backend.

Constraints for tapps-brain include: **synchronous** core, **deterministic** scoring (no LLM in the retrieve path), **single deployable artifact** with **SQLite** as the system of record, and **optional** heavy dependencies behind extras (`[vector]`, `[reranker]`).

## Decision

1. **Shipped / maintained path (do):** Stay **embedded SQLite–first**:
   - **Lexical:** FTS5 plus in-process Okapi BM25 (`bm25.py`).
   - **Dense (built-in):** `sentence-transformers`, `numpy`, and `sqlite-vec` are core dependencies (since v2.2.0) — embeddings in `embeddings.py`, vectors on entries and in **`sqlite-vec`** (`memory_vec`) by default; optional **FAISS** via `[faiss]` extra for in-process index use cases.
   - **Hybrid:** **Reciprocal Rank Fusion** in `fusion.py`, including weighted RRF and profile-driven pool sizes via `HybridFusionConfig` / `profile.hybrid_fusion`.
   - **Post-retrieval:** Composite scoring in `retrieval.py` (fixed weights + profile hooks where wired); optional **Cohere** rerank behind `[reranker]`.

2. **Out of scope for core product / v2 delivery as mandatory or default paths (defer / wontfix for now):**
   - **Learned sparse** neural retrievers as a built-in encoding path.
   - **ColBERT-style** multi-vector late interaction as a built-in storage and retrieve model.
   - **Managed external vector DB** as a required or bundled backend (operators may still mirror or export data out-of-band; that is not a supported first-class index in this ADR’s scope).

Revisit only under **new** product or scale evidence (e.g. benchmark-driven need for an external index, or an explicit product commitment to a hosted retrieval tier), via a **new** epic and ADR — not as an implicit extension of this decision.

## Consequences

- **`retrieval.py` / `fusion.py`:** Remain the canonical implementation for lexical, dense (when enabled), hybrid merge, and composite ranking. No new mandatory code paths for SPLADE, ColBERT, or remote vector services.
- **`[faiss]` extra:** Means *optional FAISS* for in-process index use cases — sentence-transformers, numpy, and sqlite-vec are now core dependencies, not optional. Not a managed service client.
- **Documentation:** Section 10 item 1 in `features-and-technologies.md` reflects this boundary; section 1 cross-links here for the maintainer decision.
- **Spike theme dropped for now:** A pluggable `VectorIndex` adapter behind `sqlite_vec_index.py` is **not** required to satisfy STORY-051.1; if external indexes become a goal, specify interfaces and migration in a follow-up ADR.

## References

- [`features-and-technologies.md`](../../engineering/features-and-technologies.md) — section 1 (retrieval map), section 8 (`vector` extra), section 10 checklist.
- [`EPIC-051.md`](../epics/EPIC-051.md) — STORY-051.1 acceptance and verification.
