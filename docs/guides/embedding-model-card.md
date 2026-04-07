# Embedding model card (default semantic search)

This page documents the **default** dense embedding stack for built-in vector / hybrid retrieval (**EPIC-042** STORY-042.2). It is the operator-facing counterpart to `src/tapps_brain/embeddings.py`.

## Default model

| Field | Value |
| --- | --- |
| **Hugging Face / ST id** | `sentence-transformers/all-MiniLM-L6-v2` |
| **Code default** | `all-MiniLM-L6-v2` (`embeddings._DEFAULT_MODEL`) |
| **Output dimension** | **384** |
| **Pooling** | Mean pooling (model-defined) |
| **Normalization** | **L2-normalized** float vectors (`normalize_embeddings=True` in `SentenceTransformerProvider`) — aligns cosine similarity with dot product on stored vectors. |
| **Typical max sequence length** | **256** subword tokens (model config; do not rely on long-context paste without a different model). |
| **License** | Apache-2.0 (sentence-transformers model card; verify upstream before redistribution). |

## Install surface

- **Install:** Included in core `pip install tapps-brain` (sentence-transformers, numpy, sqlite-vec are core dependencies since v2.2.0). Optional FAISS: `pip install tapps-brain[faiss]`.
- **Provider:** `get_embedding_provider(..., provider="sentence_transformers", model=...)` — only this provider is wired today; unknown providers return `None`.

## Storage and precision

- Vectors are stored as **float** components on `MemoryEntry.embedding` and in SQLite where the schema enables it.
- **Int8 spike (STORY-042.2):** `quantize_embedding_int8` / `dequantize_embedding_int8` / `renormalize_embedding_l2` in `embeddings.py` implement symmetric **scale-127** quantization on components clamped to **[-1, 1]** (matches L2-normalized ST outputs). Unit tests document **self-cosine ≥ 0.998** and **pairwise cosine drift under 0.02** on seeded random unit vectors. **Not** wired into persistence or sqlite-vec — changing defaults requires a follow-up design.

## Model upgrades and reindexing

- Schema **v17** adds nullable **`embedding_model_id`** on `memories` (and `archived_memories`); new saves with an embedding provider that exposes **`model_id`** (e.g. `SentenceTransformerProvider`) persist the model name. **NULL** means legacy or unknown — mixed vector spaces are still possible in one store.
- After changing the default or profile-selected model, plan a **full reindex** (re-save or batch re-embed) and use **`embedding_model_id`** to find rows that still need migration.

## Performance review backlog

Items below are **not committed work** — capture tradeoffs for a later design/perf review (EPIC-042 / EPIC-050 / product triage).

| Area | Idea | Why review later |
| --- | --- | --- |
| **On-disk embedding format** | Store **packed int8** (or float16) blobs instead of float JSON arrays | Smaller rows and less parse/serialize CPU; must reconcile with **sqlite-vec** (currently float32 `serialize_float32`) and hybrid scoring paths — likely dequant at read or dual representation. |
| **sqlite-vec alignment** | If int8 persistence ships, decide **index build** from quantized vs float32 copies | ANN quality vs storage; operator rebuild steps: [`sqlite-vec-operators.md`](sqlite-vec-operators.md). |
| **Save-path CPU** | **Batch** `embed_batch` for imports / bulk reindex instead of per-row `embed` | Cuts model invocation overhead when re-embedding thousands of rows. |
| **Model lifecycle** | Explicit **singleton / lazy** embedding provider for long-lived MCP processes | Avoid duplicate model loads if multiple code paths construct providers; measure resident memory vs first-request latency. |
| **Reindex operations** | Batched SQLite **transactions**, optional **index on `embedding_model_id`**, streaming progress | Full-store reindex is write-heavy; tune batch size and WAL checkpointing under load. |
| **Lock hold time** | Profile whether **embedding** should run outside `MemoryStore` narrow critical sections | Today embed runs inside save orchestration; shorter lock holds help contested multi-thread MCP (see concurrency doc) — **design-sensitive** (consistency vs latency). |
| **Read path** | Cache or memoize query embeddings per recall request when the same query hits BM25 + vector | Small win; only if profiling shows redundant `encode` calls. |

**Related signals:** `tests/unit/test_concurrent.py` stress test uses a **60s** wall-clock bound so a **10×50** concurrent save suite stays green under a full Windows pytest run — if that bound keeps growing, treat it as a sign to profile **SQLite + store lock** contention, not just test timeout tuning.

## Related docs

- Engineering inventory: [`features-and-technologies.md`](../engineering/features-and-technologies.md)
- Concurrency / contention context: [`system-architecture.md`](../engineering/system-architecture.md) § *Concurrency model*
- SQLite vector path: `sqlite_vec_index.py`; operator playbook [`sqlite-vec-operators.md`](sqlite-vec-operators.md); epic `docs/planning/epics/EPIC-042.md` (STORY-042.3)
