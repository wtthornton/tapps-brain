# Profile Limits: Research and Rationale

This document explains the evidence behind tapps-brain's built-in profile defaults.
Values were calibrated against hardware benchmarks, comparable AI memory systems,
academic retrieval literature, and practical PKM research.

> **Last updated:** 2026-03-24 (v1.4.2)

---

## Hardware context

tapps-brain runs on everything from Raspberry Pi 5 to cloud servers.
The pure-Python BM25 scorer is the performance-limiting factor (not SQLite).

### BM25 search latency by entry count

BM25 scoring is O(N x Q) where N = entries, Q = query terms. The index is cached
and rebuilt only when entries change, but `score()` always iterates all N entries
(IDF requires the full corpus).

| Entries | Pi 5 (8 GB, SD) | Desktop / laptop | Server | Notes |
|---------|-----------------|-------------------|--------|-------|
| 500 | ~2-5 ms | <1 ms | <1 ms | Old default |
| 2,000 | ~5-15 ms | ~2-4 ms | ~1-2 ms | |
| 5,000 | ~15-30 ms | ~5-10 ms | ~2-5 ms | **New default** |
| 10,000 | ~30-80 ms | ~10-25 ms | ~5-12 ms | research-knowledge default |
| 25,000 | ~100-250 ms | ~30-70 ms | ~15-35 ms | Enable vector search here |
| 50,000 | ~500 ms-2 s | ~100-300 ms | ~50-150 ms | Needs vector index |

*Estimates derived from rank-bm25 BEIR benchmarks (NFCorpus 3.6K = 4.5 ms/query,
SciFact 5.2K = 21 ms/query on Xeon 2.7 GHz), adjusted for tapps-brain's short
documents (~200-500 chars vs multi-paragraph academic papers).*

### SQLite is not the bottleneck

SQLite FTS5 uses an inverted index and returns results in sub-millisecond time
even at 50K-100K rows. A real-world deployment measured **4 ms vs 50 ms** (92%
improvement) after switching to FTS5. On 18.2M rows, FTS5 trigram queries
returned in **10-30 ms** vs 1,750 ms for LIKE scans.

tapps-brain uses FTS5 for candidate pre-filtering, then re-scores with the
pure-Python BM25 scorer. The Python scorer is the ceiling, not SQLite.

### Storage footprint

At ~4 KB average per entry (value + metadata + FTS5 index overhead):

| Entries | Database size | Practical on Pi SD card? |
|---------|--------------|--------------------------|
| 5,000 | ~20 MB | Yes |
| 10,000 | ~40 MB | Yes |
| 50,000 | ~200 MB | Yes (keep under 100 MB recommended) |

SQLite with WAL mode handles databases up to 100 MB comfortably on Pi 5 with
a decent SD card (Class 10 / A2). NVMe via PCIe HAT+ pushes this to 800+ MB/s.

---

## Comparable systems

| System | Default / typical limit | Architecture |
|--------|------------------------|--------------|
| **Mem0 (free tier)** | 10,000 memories | Vector DB + LLM extraction |
| **Mem0 (self-hosted)** | No documented limit | Vector DB |
| **MemGPT / Letta archival** | Unbounded | Vector DB (LanceDB) |
| **MemGPT core memory** | 5,000 chars/block | In-context LLM memory |
| **Obsidian** | ~10K-12K notes comfortable | File-based markdown |
| **Zep** | No hard limit | Temporal knowledge graph |
| **LangChain window** | k=5 turns | In-memory buffer |
| **tapps-brain (v1.4.1)** | 500 entries | SQLite + BM25 |
| **tapps-brain (v1.4.2)** | **5,000 entries** | SQLite + BM25 |

The old default of 500 was the most conservative limit of any comparable system.
5,000 aligns with the practical comfortable range for file-based knowledge stores
(Obsidian) and is well below vector-backed systems (Mem0, Letta).

---

## max_entries rationale

**Old default: 500. New default: 5,000.**

- GC and auto-consolidation keep the active set lean: stale entries decay and
  get archived, similar entries get merged. The limit is a safety net, not a target.
- 5,000 gives room for 6+ agents over months of use while keeping BM25 search
  under 30 ms even on Pi 5.
- Anyone hitting 5,000+ active entries after GC has a real knowledge base and
  should enable the vector/embedding search path.
- research-knowledge uses 10,000 because knowledge accumulation is the whole point.

| Profile | Old | New | Rationale |
|---------|-----|-----|-----------|
| repo-brain | 500 | 5,000 | Multiple agents, months of repo conventions |
| personal-assistant | 500 | 5,000 | Identity + preferences accumulate over years |
| research-knowledge | 1,000 | 10,000 | Knowledge accumulation is the purpose |
| customer-support | 500 | 5,000 | Product knowledge + interaction history |
| home-automation | 750 | 5,000 | Sensor events + learned patterns across devices |
| project-management | 500 | 5,000 | Decisions + plans across long projects |

**For larger systems:** Desktop and server hardware can comfortably handle 10,000-25,000
entries. Override `limits.max_entries` in a custom profile or `extends` block.
At 25,000+ entries, enable vector search (`uv sync --extra vector`) for sub-linear
retrieval.

---

## default_token_budget rationale

**Old default: 2,000. New default: 3,000.**

RAG best practice allocates 50-75% of available context for retrieved content
(after system prompt and output buffer). For modern LLMs with 128K+ context
windows, 2,000 tokens of memory injection is less than 2% -- quite conservative.

However, tapps-brain injects alongside other context (code, conversation history),
so the budget controls memory specifically, not total context. The "Lost in the
Middle" problem (Anthropic, 2024) also shows that 5 highly relevant chunks
outperform 50 stuffed documents.

| Profile | Old | New | Rationale |
|---------|-----|-----|-----------|
| repo-brain | 2,000 | 3,000 | Room for architectural + pattern knowledge |
| personal-assistant | 3,000 | 4,000 | Identity + preferences + recent context compete |
| research-knowledge | 2,000 | 4,000 | Research queries need richer context |
| customer-support | 2,000 | 3,000 | Product knowledge + interaction history |
| home-automation | 2,000 | 2,000 | Short, focused entries; budget is adequate |
| project-management | 2,000 | 3,000 | Decisions + plans can be verbose |

---

## Source trust / confidence / ceilings rationale

Previously identical across all 6 profiles. Now differentiated where the domain
semantics warrant it.

**Baseline (unchanged profiles: repo-brain, project-management):**

| Source | Trust | Initial confidence | Ceiling |
|--------|-------|--------------------|---------|
| human | 1.0 | 0.95 | 0.95 |
| system | 0.9 | 0.90 | 0.95 |
| agent | 0.7 | 0.60 | 0.85 |
| inferred | 0.5 | 0.40 | 0.70 |

These align with Google's Knowledge-Based Trust research (VLDB 2015) and WebTrust
(IBM), where official/verified sources score 0.85-1.0, curated sources 0.7-0.85,
and unverified sources 0.3-0.6.

**Per-profile overrides:**

| Profile | Change | Rationale |
|---------|--------|-----------|
| personal-assistant | human confidence 0.95 → 0.98, ceiling 0.95 → 0.98 | User's own stated preferences deserve highest possible confidence |
| customer-support | agent trust 0.7 → 0.80, confidence 0.60 → 0.70, ceiling 0.85 → 0.90 | Most entries come from agents processing tickets; under-weighting them degrades the whole system |
| home-automation | system trust 0.9 → 0.95, confidence 0.90 → 0.95 | Sensor data from system sources is ground truth in IoT |
| research-knowledge | inferred ceiling 0.70 → 0.55 | Research should be conservative about unverified claims |

---

## GC threshold rationale

Previously identical across 5 of 6 profiles (only home-automation differed on
session expiry). Comparable: Mem0 uses 7-day short-term, 30-day medium-term,
no-expiry permanent. Zep uses bitemporal invalidation (no time-based decay).

| Profile | Old floor | New floor | Old session | New session | Rationale |
|---------|-----------|-----------|-------------|-------------|-----------|
| repo-brain | 30 | 30 | 7 | 7 | Well-calibrated as-is |
| personal-assistant | 30 | **60** | 7 | **14** | Identity memories shouldn't GC after 30 days; conversations span longer |
| research-knowledge | 30 | **60** | 7 | 7 | Established facts should persist |
| customer-support | 30 | **14** | 7 | **3** | Stale ticket context should clear faster |
| home-automation | 30 | **7** | 1 | 1 | Transient sensor data clears aggressively |
| project-management | 30 | 30 | 7 | 7 | Well-calibrated as-is |

---

## Recall min_score / min_confidence rationale

| Profile | Old score | New score | Old conf | New conf | Rationale |
|---------|-----------|-----------|----------|----------|-----------|
| repo-brain | 0.3 | 0.3 | 0.1 | 0.1 | Baseline; well-calibrated |
| personal-assistant | 0.3 | **0.2** | 0.1 | 0.1 | Recency-heavy scoring handles relevance; lower bar catches more |
| research-knowledge | 0.3 | **0.35** | 0.1 | **0.25** | Stricter to filter low-quality / unverified results |
| customer-support | 0.3 | **0.25** | 0.1 | **0.15** | Slightly stricter confidence; looser score for broader recall |
| home-automation | 0.3 | **0.2** | 0.1 | 0.1 | Recency-dominant scoring handles relevance |
| project-management | 0.3 | 0.3 | 0.1 | 0.1 | Baseline; well-calibrated |

---

## max_value_length, max_tags, max_key_length

These are unchanged. Rationale:

- **max_value_length (4,096 / 8,192 for research):** ~1,000 tokens, fits 2-3
  paragraphs. Memory entries are summaries, not full documents. RAG chunk size
  best practice: 256-512 tokens.
- **max_tags (10):** PKM experts (Tiago Forte / Forte Labs) recommend 1-3 tags
  per item, practical upper bound 3-5. 10 is already 2-3x the sweet spot.
  GitHub allows 100 labels; Jira UI caps at ~55. 10 is a reasonable safety net.
- **max_key_length (128):** Slug-format keys. 128 chars is generous.

---

## BM25 parameters

**k1 = 1.2, b = 0.75** (standard Okapi BM25 defaults, matching Elasticsearch,
Lucene, and Tantivy). A large-scale ECIR 2020 reproducibility study found
"no significant differences between any BM25 variant." Not worth tuning.

**bm25_norm_k = 5.0** is a normalization constant mapping raw BM25 scores to
[0, 1] via `score / (score + K)`. A raw BM25 score of 5.0 maps to 0.5
normalized. This is reasonable for typical BM25 score ranges (0-15+) on short
documents.

---

## Sources

- rank-bm25 / BM25S benchmarks: [arXiv 2407.03618](https://arxiv.org/html/2407.03618v1)
- SQLite FTS5 performance: [Andrew Mara blog](https://andrewmara.com/blog/faster-sqlite-like-queries-using-fts5-trigram-indexes)
- SQLite on Raspberry Pi: [Atomic Object](https://spin.atomicobject.com/sqlite-raspberry-pi/)
- Google Knowledge-Based Trust: [VLDB 2015](https://www.vldb.org/pvldb/vol8/p938-dong.pdf)
- Mem0 platform limits: [docs.mem0.ai](https://docs.mem0.ai/platform/overview)
- MemGPT / Letta memory management: [docs.letta.com](https://docs.letta.com/advanced/memory-management/)
- Obsidian vault limits: [forum.obsidian.md](https://forum.obsidian.md/t/maximum-number-of-notes-in-vault/1509)
- BM25 parameter tuning: [Elastic blog](https://www.elastic.co/blog/practical-bm25-part-3-considerations-for-picking-b-and-k1-in-elasticsearch)
- BM25 variant comparison (ECIR 2020): [PMC/7148026](https://pmc.ncbi.nlm.nih.gov/articles/PMC7148026/)
- PKM tagging best practices: [Forte Labs](https://fortelabs.com/blog/a-complete-guide-to-tagging-for-personal-knowledge-management/)
- RAG token budget guidance: [getmaxim.ai](https://www.getmaxim.ai/articles/context-window-management-strategies-for-long-context-ai-agents-and-chatbots/)
- Ebbinghaus-inspired memory decay: [Mem0 paper, arXiv 2504.19413](https://arxiv.org/abs/2504.19413)
