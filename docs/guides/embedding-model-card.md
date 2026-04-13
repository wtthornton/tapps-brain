# Embedding model card (default semantic search)

This page documents the **default** dense embedding stack for built-in vector / hybrid retrieval (**EPIC-042** STORY-042.2). It is the operator-facing counterpart to `src/tapps_brain/embeddings.py`.

## Default model

| Field | Value |
| --- | --- |
| **Hugging Face / ST id** | `BAAI/bge-small-en-v1.5` |
| **Code default** | `BAAI/bge-small-en-v1.5` (`embeddings._DEFAULT_MODEL`) |
| **Output dimension** | **384** |
| **Pooling** | CLS pooling (model-defined) |
| **Normalization** | **L2-normalized** float vectors (`normalize_embeddings=True` in `SentenceTransformerProvider`) — aligns cosine similarity with dot product on stored vectors. |
| **Typical max sequence length** | **512** subword tokens (model config). |
| **MTEB score** | ~62 (vs 56.3 for prior default all-MiniLM-L6-v2) |
| **License** | MIT (BAAI model card; verify upstream before redistribution). |

## Install surface

- **Install:** Included in core `pip install tapps-brain` (`sentence-transformers`, `numpy`, and `psycopg[binary,pool]` are core dependencies).
- **Provider:** `get_embedding_provider(..., provider="sentence_transformers", model=...)` — only this provider is wired today; unknown providers return `None`.

## Storage and precision

- Vectors are stored as **float32** in the pgvector `vector(384)` column (`private_memories.embedding`, HNSW cosine index — migration 002).
- **Int8 spike (STORY-042.2):** `quantize_embedding_int8` / `dequantize_embedding_int8` / `renormalize_embedding_l2` in `embeddings.py` implement symmetric **scale-127** quantization on components clamped to **[-1, 1]** (matches L2-normalized ST outputs). Unit tests document **self-cosine ≥ 0.998** and **pairwise cosine drift under 0.02** on seeded random unit vectors. **Not yet wired into persistence** — changing defaults requires a follow-up design.

## Model upgrades and reindexing

- The `private_memories` table stores a nullable **`embedding_model_id`** column; new saves with an embedding provider that exposes **`model_id`** (e.g. `SentenceTransformerProvider`) persist the model name. **NULL** means legacy or unknown — mixed vector spaces are still possible in one store.
- After changing the default or profile-selected model, plan a **full reindex** (re-save or batch re-embed) and use **`embedding_model_id`** to find rows that still need migration.

## Performance review backlog

Items below are **not committed work** — capture tradeoffs for a later design/perf review (EPIC-042 / EPIC-050 / product triage).

| Area | Idea | Why review later |
| --- | --- | --- |
| **On-disk embedding format** | Store **packed int8** (or float16) in pgvector instead of float32 | Smaller rows and less parse/serialize CPU; pgvector supports `halfvec` (float16) and `bit` (binary) index types — evaluate quality vs storage trade-off before wiring to quantized ints. |
| **pgvector alignment** | If int8 persistence ships, decide **index build** from quantized vs float32 copies | ANN quality vs storage; operator rebuild steps: see Postgres backup/restore runbook. |
| **Save-path CPU** | **Batch** `embed_batch` for imports / bulk reindex instead of per-row `embed` | Cuts model invocation overhead when re-embedding thousands of rows. |
| **Model lifecycle** | Explicit **singleton / lazy** embedding provider for long-lived MCP processes | Avoid duplicate model loads if multiple code paths construct providers; measure resident memory vs first-request latency. |
| **Reindex operations** | Batched Postgres **transactions**, optional **filter on `embedding_model_id`**, streaming progress | Full-store reindex is write-heavy; tune batch size and Postgres connection pool under load. |
| **Lock hold time** | Profile whether **embedding** should run outside `MemoryStore` narrow critical sections | Today embed runs inside save orchestration; shorter lock holds help contested multi-thread MCP (see concurrency doc) — **design-sensitive** (consistency vs latency). |
| **Read path** | Cache or memoize query embeddings per recall request when the same query hits BM25 + vector | Small win; only if profiling shows redundant `encode` calls. |

**Related signals:** `tests/unit/test_concurrent.py` stress test uses a **60s** wall-clock bound so a **10×50** concurrent save suite stays green under a full Windows pytest run — if that bound keeps growing, treat it as a sign to profile **Postgres write latency + store lock** contention, not just test timeout tuning.

## Related docs

- Engineering inventory: [`features-and-technologies.md`](../engineering/features-and-technologies.md)
- Concurrency / contention context: [`system-architecture.md`](../engineering/system-architecture.md) § *Concurrency model*
- Postgres vector path: `postgres_private.py` (pgvector HNSW); epic `docs/planning/epics/EPIC-042.md` (STORY-042.3)
