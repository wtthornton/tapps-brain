---
id: EPIC-042
title: "Retrieval and ranking (RAG-style memory) — research and upgrades"
status: done
priority: high
created: 2026-03-31
tags: [retrieval, bm25, hybrid, embeddings, rerank, injection, decay, rag]
completed: 2026-04-09
---

# EPIC-042: Retrieval and ranking (RAG-style memory)

## Context

Maps to **§1** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). Core product value is **retrieving the right memories** under **bounded context** without LLM-in-the-loop scoring today (`bm25.py`, `retrieval.py`, `fusion.py`, `injection.py`, `decay.py`).

## Success criteria

- [x] Each story below has either a **closed GitHub issue** with outcome, or an explicit **wontfix / satisfied** note in the epic.
- [x] At least one **benchmark or offline eval** run documents retrieval quality before/after for any merged upgrade path (`evaluation.py` harness). *(Satisfied: `lexical_golden_eval_suite()` + `load_eval_suite_into_store()` in `evaluation.py`; golden corpus at `tests/eval/`; script `scripts/run_eval_golden.py`.)*
- [x] `features-and-technologies.md` §1 updated if default behaviors or deps change. *(Satisfied: §1 table reflects FlashRank reranker, HybridFusionConfig, LexicalRetrievalConfig, FSRS-lite, and embedding-model-card.md as of 2026-04-09.)*

## Stories

**§1 table order:** **042.1** lexical/FTS+BM25 → **042.2** dense semantic → **042.3** sqlite-vec → **042.4** hybrid RRF → **042.5** composite ranking → **042.6** rerank → **042.7** token-budget injection → **042.8** decay/FSRS.

### STORY-042.1: Lexical / keyword search (FTS5 + Okapi BM25)

**Status:** done (2026-04-02)  
**Effort:** L  
**Depends on:** none  
**Context refs:** `src/tapps_brain/bm25.py`, `src/tapps_brain/persistence.py`, `src/tapps_brain/retrieval.py`, `tests/unit/test_memory_bm25.py`, `tests/unit/test_memory_retrieval.py`  
**Verification:** `pytest tests/unit/test_memory_bm25.py tests/unit/test_memory_retrieval.py -v --tb=short -m "not benchmark"`

#### Code baseline

- **FTS5** backs fast text paths; **Okapi BM25** is reimplemented in pure Python with ~50 stop words and light normalization.
- Rank fusion and hybrid paths assume BM25 scores are **comparable** within a corpus rebuild cycle.

#### Research notes (2026-forward)

- **Production hybrid RAG** commonly uses **wide recall** (e.g. top-100 per channel) then **RRF** then **cross-encoder rerank**; lexical channel remains essential for **codes, IDs, rare tokens** where dense models blur.
- **Learned sparse** encoders (e.g. SPLADE-style) are an alternative sparse channel; tradeoffs: model size, CPU/GPU, cold start vs BM25 zero-shot.
- **Mathematics:** BM25 term weight ∝ `IDF * (f * (k1+1)) / (f + k1 * (1 - b + b * dl/avgdl))` — audit **k1, b** and field length definition vs our average document length for coding memories.

#### Implementation themes (fix / enhance / improve)

- [x] Spike: **tokenization** for code (identifiers, paths) vs whitespace split — `lexical.py` (`tokenize_lexical`, camelCase boundaries), BM25 + FTS query terms wired from profile (2026-04-02).
- [x] Document **when FTS vs BM25 full scan** runs; eliminate unnecessary full-corpus rescans if hotspots exist. *(2026-04-01: `docs/engineering/call-flows.md` recall § + `_bm25_score_entries` docstring.)*
- [x] Optional: **stem / ASCII fold** behind profile — `LexicalRetrievalConfig` (`apply_stem`, `ascii_fold`, `camel_case_tokenization`, `fts_path_splits`) on `MemoryProfile`, passed to `MemoryPersistence` and `MemoryRetriever` (2026-04-02).
- [x] Add regression **golden set** in `evaluation.py` for lexical-only queries (SKUs, error strings) — `lexical_golden_eval_suite()` + `load_eval_suite_into_store()` (2026-04-02).

---

### STORY-042.2: Dense retrieval / semantic search

**Status:** done (2026-04-02) — model card, int8 quantization spike helpers, ``embedding_model_id`` (schema v17), store wiring  
**Effort:** L  
**Depends on:** none  
**Context refs:** `src/tapps_brain/embeddings.py`, `src/tapps_brain/_feature_flags.py`, `pyproject.toml` (core deps since v2.2.0), `tests/unit/test_memory_embeddings.py`, `tests/unit/test_memory_embeddings_persistence.py`  
**Verification:** `pytest tests/unit/test_memory_embeddings.py tests/unit/test_memory_embeddings_persistence.py -v --tb=short -m "not benchmark"`

#### Code baseline

- **`sentence-transformers`** + **`numpy`** are core dependencies (since v2.2.0); **`faiss-cpu`** optional via `[faiss]` extra.
- Embeddings stored on entries and used when hybrid config enables semantic search.

#### Research notes (2026-forward)

- **Model churn:** Multilingual and **long-context** embedding models evolve yearly; pin **evaluated** defaults and document **dimension** and **MRL truncation** if adopted.
- **Mathematics:** Cosine vs L2 in ANN — ensure **normalized vectors** if inner-product indexes are used; mismatch causes silent recall loss.
- **Late interaction** (ColBERT-style) improves precision but increases storage/compute — likely **out of scope** for embedded SQLite-first product unless service mode appears.

#### Implementation themes

- [x] **Model card** in docs: default model name, dim, max tokens, license — [`embedding-model-card.md`](../../guides/embedding-model-card.md) (2026-04-02).
- [x] Spike: **quantization** (int8) — symmetric scale-127 helpers in ``embeddings.py`` + deterministic quality bounds in unit tests; **on-disk storage remains float JSON** until a product decision adopts int8 blobs.
- [x] **Embedding model id** — nullable ``memories.embedding_model_id`` (schema **v17**), ``MemoryEntry.embedding_model_id``, set from ``SentenceTransformerProvider.model_id`` / optional ``NoopProvider(model_id=…)`` on embed path.
- **Performance backlog (review later):** optional on-disk int8/float16, batch reindex, lock-hold vs embed, sqlite-vec alignment — tracked in [`embedding-model-card.md`](../../guides/embedding-model-card.md) § *Performance review backlog* (not scheduled implementation).

---

### STORY-042.3: Vector index in database (sqlite-vec)

**Status:** done (2026-04-02) — operator playbook, incremental cost notes, L2 distance vs SQL/docs  
**Effort:** M  
**Depends on:** STORY-042.2 (conceptual)  
**Context refs:** `src/tapps_brain/sqlite_vec_index.py`, `src/tapps_brain/persistence.py`, `src/tapps_brain/health_check.py`, `tests/unit/test_sqlite_vec_index.py`, `tests/unit/test_persistence_sqlite_vec.py`, `tests/unit/test_sqlite_vec_try_load.py`  
**Verification:** `pytest tests/ -k sqlite_vec -v --tb=short -m "not benchmark"`

#### Code baseline

- **`memory_vec` / vec0** when `sqlite-vec` importable; KNN path wired from store; health surfaces row counts and mode strings (#63).

#### Research notes (2026-forward)

- **DB-native vectors** reduce moving parts vs external Milvus/PGVector but bound **scale** to single-node SQLite expectations.
- Compare **HNSW** (where available externally) vs sqlite-vec **latency/recall** on N≈5k–50k rows for sizing guidance.

#### Implementation themes

- [x] Operator doc: **rebuild / vacuum** playbook — `docs/guides/sqlite-vec-operators.md` *(guide removed — SQLite retired in ADR-007)* (2026-04-02).
- [x] Notes: **incremental** index updates vs per-save cost (delete+insert upsert, no batching) in operator doc + `persistence.py` / `sqlite_vec_index.py` docstrings (2026-04-02).
- [x] Align **distance metric** with vec0 default **L2** and actual `MATCH` SQL + `retrieval.py` comment on `1/(1+dist)` (2026-04-02).

---

### STORY-042.4: Hybrid search (RRF + weighted RRF)

**Status:** done (2026-04-02) — RRF formula + citation in `fusion.py`; `HybridFusionConfig` / `profile.hybrid_fusion` wired through `inject_memories`  
**Effort:** M  
**Depends on:** STORY-042.1, STORY-042.2  
**Context refs:** `src/tapps_brain/fusion.py`, `src/tapps_brain/profile.py`, `src/tapps_brain/injection.py`, `src/tapps_brain/retrieval.py`, `tests/unit/test_memory_fusion.py`, `tests/unit/test_memory_retrieval.py`, `tests/unit/test_profile.py`  
**Verification:** `pytest tests/unit/test_memory_fusion.py tests/unit/test_memory_retrieval.py tests/unit/test_profile.py -v --tb=short -m "not benchmark"`

#### Code baseline

- **RRF** merges ranked lists; **query-aware weights** (`hybrid_rrf_weights_for_query`, #40) bias BM25 vs vector without ML.

#### Research notes (2026-forward)

- Industry writeups emphasize RRF because score scales differ; **code today:** `fusion.py` uses **k = 60** (documented alongside Elasticsearch/Azure defaults). This story validates **weighted** RRF behavior and whether **k** / per-channel top-k should be profile-tunable.
- **Candidate pool size:** pulling too few from each channel hurts recall; too many hurts latency — **profile-tunable** `top_k_lexical` / `top_k_dense` worth evaluating.

#### Implementation themes

- [x] Document **formula** in `fusion.py` docstring with citation to standard RRF (Cormack et al.; production *k*=60 note).
- [x] Profile flags for **per-channel top-k** and **k**: `HybridFusionConfig` (`top_bm25` / `top_vector` / `rrf_k`, YAML aliases `top_k_lexical` / `top_k_dense`); `inject_memories` passes `profile.hybrid_fusion` when it is a real model instance (avoids MagicMock test doubles).
- [ ] Optional follow-up: A/B harness — same golden queries, report MRR/nDCG@k from `evaluation.py` (not blocking; deferred to EPIC-047 or standalone eval task).

---

### STORY-042.5: Composite ranking (relevance + confidence + recency + frequency)

**Status:** done (2026-04-02)  
**Effort:** M  
**Depends on:** none  
**Context refs:** `src/tapps_brain/retrieval.py` (`_W_RELEVANCE`…), `src/tapps_brain/profile.py` `ScoringConfig`, `tests/unit/test_memory_retrieval.py`  
**Verification:** `pytest tests/unit/test_memory_retrieval.py -v --tb=short -m "not benchmark"`

#### Code baseline

- Fixed **40/30/15/15** blend then **source trust** multipliers; profile may expose scoring knobs where wired.

#### Research notes (2026-forward)

- **Score calibration:** linear blend is interpretable but not **calibrated probability**; for “confidence gates,” consider **Platt-style** calibration on held-out feedback (optional, offline).
- **Position bias** in user feedback can distort frequency term — document in flywheel epic (#047).

#### Implementation themes

- [x] Expose **documented** profile tuning for weights with **sum-to-1** validation — `ScoringConfig`, `SCORING_WEIGHT_SUM_*`, `composite_scoring_weight_total()`, `repo-brain.yaml` comments, `retrieval.py` module doc + retriever warning band aligned with profile (2026-04-02).
- [x] Spike: **min-max normalization** per channel before blend vs current BM25 normalization — opt-in ``scoring.relevance_normalization: minmax`` (per-query extrema over **filtered** candidates; default ``sigmoid`` unchanged); see ``retrieval.py`` / ``ScoringConfig`` (2026-04-02).
- [x] Ensure **superseded / invalid** entries never contribute to ranking (audit `list_all` / retriever filters) — documented in `retrieval.py` module doc; default `search()` filters temporally invalid, contradicted, consolidated sources (2026-04-02).

---

### STORY-042.6: Re-ranking (Cohere + alternatives)

**Status:** done (2026-04-02) — structured logs + injection telemetry (latency, provider, counts)  
**Effort:** M  
**Depends on:** STORY-042.4  
**Context refs:** `src/tapps_brain/reranker.py`, `src/tapps_brain/injection.py`, `tests/unit/test_reranker.py`, `tests/unit/test_memory_retrieval.py`  
**Verification:** `pytest tests/unit/test_reranker.py tests/unit/test_memory_retrieval.py -v --tb=short -m "not benchmark"`

#### Code baseline

- **FlashRank** local cross-encoder when `[reranker]` extra (`flashrank` dep); noop fallback. *(Cohere was replaced by FlashRank in cleanup phase 11 — on-device, no API key.)*

#### Research notes (2026-forward)

- **Cross-encoders** remain state-of-the-art for **precision** atop hybrid recall; vendor lock-in vs **open local cross-encoder** (e.g. small transformer) is a product tradeoff.
- **Latency SLO:** rerank top-10 vs top-50 changes cost; **dynamic cap** from `InjectionConfig` / profile.

#### Implementation themes

- [x] Add **second provider** spike (local ONNX/transformers) behind protocol — same interface as `reranker.py`. *(Satisfied: FlashRank is the local cross-encoder provider; noop remains the passthrough. A third ONNX/transformers provider is deferred — no scheduled story.)*
- [x] Structured log: **rerank latency**, **provider**, **candidates_in** — ``memory_rerank`` (``info`` for non-noop provider, ``debug`` for noop); failure: ``reranker_failed_fallback_to_original`` with timing; ``MemoryRetriever.last_rerank_stats`` + ``inject_memories`` ``injection_telemetry`` keys ``rerank_*`` (2026-04-02).
- [x] Document **PII** implications — note in ``reranker.py`` module doc (cloud rerank sends snippets).

---

### STORY-042.7: Token-budgeted context (injection)

**Status:** done (2026-04-02)  
**Effort:** S  
**Depends on:** none  
**Context refs:** `src/tapps_brain/injection.py`, `tests/unit/test_memory_injection.py`  
**Verification:** `pytest tests/unit/test_memory_injection.py -v --tb=short -m "not benchmark"`

#### Code baseline

- **`injection_max_tokens`**, per-tier **max counts**, **`_MIN_SCORE`** gate; **estimate_tokens** heuristic.

#### Research notes (2026-forward)

- Prefer **tokenizer-aligned** counting when talking to specific models (e.g. tiktoken for GPT family) — heuristic byte/char estimates drift for non-English.
- **Mathematics:** knapsack-style packing (value = score, weight = tokens) could beat greedy truncation for **optimal** budget use — optional advanced story.

#### Implementation themes

- [x] Optional **tokenizer backend** hook (offline, explicit dep) for accurate budgets — ``InjectionConfig.count_tokens`` + ``_entry_token_cost`` (2026-04-02).
- [x] Injection **telemetry**: ``injection_telemetry`` dict (``dropped_below_min_score``, ``dropped_by_safety``, ``omitted_by_token_budget``, ``token_counter``) (2026-04-02).
- [x] Document **order** of memories in injected block (score desc vs diversity) — module doc + ``inject_memories`` returns (2026-04-02).

---

### STORY-042.8: Stale / decayed relevance (exponential decay + FSRS fields)

**Status:** done (2026-04-02)  
**Effort:** M  
**Depends on:** none  
**Context refs:** `src/tapps_brain/decay.py`, `src/tapps_brain/models.py` (stability/difficulty), `tests/unit/test_memory_decay.py`  
**Verification:** `pytest tests/unit/test_memory_decay.py -v --tb=short -m "not benchmark"`

#### Code baseline

- **Lazy exponential decay** on read; tier half-lives; **FSRS-like** fields on model partially wired.

#### Research notes (2026-forward)

- **FSRS-4.5 / 5** literature uses **review outcomes** to update stability; we have **access/feedback** signals — map to a **deterministic** update rule or stay tier-only.
- Avoid **double-counting** decay if composite score also penalizes age.

#### Implementation themes

- [x] Decision doc: **FSRS full** vs **tier half-life only** vs **hybrid** — [`docs/guides/memory-decay-and-fsrs.md`](../../guides/memory-decay-and-fsrs.md) (2026-04-02).
- [x] FSRS-lite: **update on recall** (`record_access`) vs **update on reinforce** (`reinforce`, `was_useful=True`) + tests (2026-04-02).
- [x] Profile: **per-tier** half-life overrides — documented in guide (operators tune `layers[].half_life_days`; unknown tiers fall back with warning) (2026-04-02).

## Priority order

Respects **Depends on** edges: **042.4** needs **042.1** + **042.2**; **042.6** needs **042.4**; **042.3** follows **042.2**.

1. **042.1**, **042.5**, **042.7**, **042.8** — lexical channel, composite weights, injection caps, decay/FSRS (no cross-deps).  
2. **042.2**, **042.3** — embeddings then sqlite-vec index.  
3. **042.4** — hybrid RRF (requires BM25 + dense paths).  
4. **042.6** — rerank after hybrid candidate lists exist.
