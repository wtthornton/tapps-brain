# Agent memory systems — 2026 knowledge base

> Compiled 2026-04-17 for tapps-brain architecture discussions. Every non-obvious
> claim carries an inline date + source. Items that could not be verified
> from a primary source are marked `[unverified]`.

## Executive summary

The agent-memory field in 2025–2026 has split along three axes that were open
questions in 2024:

1. **Storage topology.** Two camps emerged. The **graph camp** (Zep/Graphiti,
   Cognee, HippoRAG, A-MEM, MAGMA) argues that entity/relation graphs with
   temporal edges out-retrieve flat vectors on multi-session reasoning. The
   **hybrid-store camp** (Mem0, LlamaIndex, LangGraph) keeps vectors as the
   backbone and bolts a graph layer on as optional. Both cite LoCoMo scores in
   the high 80s–low 90s with GPT-4-class judges. tapps-brain sits in neither
   camp — it is a **tsvector + pgvector hybrid with RRF**, no entity graph.
2. **Consolidation.** The direction of travel in published 2025 work is
   **LLM-driven** consolidation (Mem0's `ADD/UPDATE/DELETE/NOOP`, A-MEM's
   Zettelkasten linking, MemRL's learned policy). tapps-brain's
   **deterministic text-similarity merge** is now an outlier. This is a
   deliberate, defensible choice (200-agent target, no LLM in the data path),
   but it is an outlier.
3. **Memory as a service vs. in-agent state.** Claude (Mar 2026), ChatGPT,
   Letta, Zep Cloud, Supermemory, MemMachine and mem0's hosted tier all
   ship memory as a platform feature. OpenAI's Assistants API — the first
   "memory API" — is being deprecated on 2026-08-26 in favour of the
   Responses + Conversations API pair, i.e. the provider now owns the memory
   schema, not the developer. tapps-brain's "memory layer the agent owns,
   deployed per box" is the *other* pole of this axis.

The single biggest finding from the academic side is that **power-law decay
fits human forgetting data strictly better than exponential** (Wixted &
Ebbesen 1997; Kahana & Adler), and several 2025 cognitive-architecture
papers have picked it up. tapps-brain uses exponential decay with
per-tier half-lives, which the psychology literature would call a simpler-
but-wrong model. See §1.

The single biggest finding from the industry side is that **LoCoMo is now
the de facto benchmark** and most published systems cluster in the 83–92%
range with a GPT-4-class judge. There is no published tapps-brain LoCoMo
number. See §5.

## How this maps to tapps-brain

| tapps-brain choice | Industry majority (2026) | Tension |
| --- | --- | --- |
| Postgres-only, pgvector + tsvector | Hybrid vector+graph (Mem0, Zep, Cognee) | Medium. Graph layer is adoptable as a second table without breaking ADR-007. |
| RRF blending BM25+vector | RRF is the industry default for hybrid retrieval (OpenSearch 2.19, Vertex AI, Elastic) | None. Aligned. |
| Exponential decay, per-tier half-lives | Power-law (cog-sci canon); learned policies (MemRL, AgeMem) in 2025 papers | High. §1 elaborates. |
| Deterministic consolidation (text similarity) | LLM-assisted consolidation is SOTA (Mem0, A-MEM) | High, deliberate. §2, §5. |
| (project_id, agent_id) + Postgres RLS | Most frameworks do NOT enforce tenant isolation at the DB layer | Low — tapps-brain is ahead here. |
| MCP Streamable HTTP, 55 tools | MCP is now the cross-vendor standard (97M SDK downloads/mo, Mar 2026) | None. Aligned. |
| No LLM in the memory path | Every benchmark-topping system uses an LLM for write-side formation | High. Is the throughput win worth the quality ceiling? §5. |
| 200-agent concurrency target | No industry system publishes comparable concurrency numbers | Moot — nobody to compare against. |

## 1. Academic / mathematical

### 1.1 Surveys and syntheses (2025–2026)

- **Shichun Liu et al., *Memory for Autonomous LLM Agents: Mechanisms,
  Evaluation, and Emerging Frontiers*, arXiv:2603.07670** (2026). Organises
  the field into five mechanism families: context-resident compression,
  retrieval-augmented stores, reflective self-improvement, hierarchical
  virtual context, policy-learned management. Frames memory as a
  write–manage–read loop with Formation / Evolution / Retrieval sub-stages.
  Open challenges flagged: continual consolidation without catastrophic
  loss, causally grounded retrieval (vs. pure similarity), trustworthy
  reflection, learned forgetting, multimodal embodied memory.
  <https://arxiv.org/abs/2603.07670>
- **Memory in the Age of AI Agents: A Survey** (arXiv:2512.13564, Dec 2025) — companion
  survey; maintained paper list at
  <https://github.com/Shichun-Liu/Agent-Memory-Paper-List>.
- **ICLR 2026 MemAgents Workshop** (call & accepted papers) —
  <https://sites.google.com/view/memagent-iclr26/>. Focused on episodic /
  semantic / working-memory interfaces with external stores. Submission
  deadline 2026-02-13.
- **ACM TOIS, *A Survey on the Memory Mechanism of Large Language
  Model-based Agents*** (Jul 2025) —
  <https://dl.acm.org/doi/10.1145/3748302>. Taxonomy of short/long/core
  memory and associated retrieval strategies.

### 1.2 Retrieval algorithms beyond RRF

- **HippoRAG (NeurIPS 2024, arXiv:2405.14831)**. Builds a phrase-node +
  passage-node composite KG and runs Personalised PageRank over it to
  retrieve. Beats dense-retrieval baselines by up to 20% on multi-hop QA.
  *Relevance to tapps-brain:* a retrieval-only augmentation; the graph is
  built offline, query path is still a ranked list. Could be layered on
  pgvector without touching the storage schema. <https://arxiv.org/abs/2405.14831>
- **HippoRAG 2: *From RAG to Memory: Non-Parametric Continual Learning for
  LLMs*** (ICML 2025). F1 59.8 vs. 53.1 for v1 and 57.0 for NV-Embed-v2 on
  same evaluation harness; +7 F1 on MuSiQue. <https://github.com/OSU-NLP-Group/HippoRAG>
- **Fusion-function analysis — Bruch, Gai & Ingber, ACM TOIS 2023
  (*An Analysis of Fusion Functions for Hybrid Retrieval*)**. Formal
  result: convex-combination fusion with normalised scores *can* beat RRF
  when the score distributions of the two retrievers are comparable, but
  RRF's rank-only approach is within ~4% nDCG@10 in the general case and
  has no hyperparameters. **This is the canonical defence of tapps-brain's
  RRF choice.** <https://dl.acm.org/doi/10.1145/3596512>
- **OpenSearch 2.19 RRF post (2024-10-10, updated 2025)** — OpenSearch's
  RRF measured 3.86% below score-based hybrid on NDCG@10 over 6 datasets;
  still recommended as default due to score-scale robustness.
  <https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/>

### 1.3 Decay and forgetting functions

- **Wixted & Ebbesen (1991/1997), Wixted & Carpenter (2007)**. Empirically,
  human retention follows a **power function** `R = m(1 + ht)^-f` better
  than a pure exponential across procedures (free recall, savings,
  recognition). An *average* of many exponentials with different loss
  rates produces a curve that fits as a power law — so power-law is often
  interpreted as the macro, exponential as the micro.
  <http://wixtedlab.ucsd.edu/publications/wixted/Wixted_and_Carpenter_(2007).pdf>,
  <http://wixtedlab.ucsd.edu/publications/wixted/Wixted_and_Ebbesen_(1997).pdf>
- **Kahana & Adler, *Note on the power law of forgetting*** —
  <https://memory.psych.upenn.edu/files/pubs/KahaAdle02.pdf>. Power law
  fits individual-subject data; exponential fits group averages only.
- **Human-Like Remembering and Forgetting in LLM Agents: An ACT-R-Inspired
  Memory Architecture** (ACM HAI 2025) — uses ACT-R's activation decay
  (power-law + frequency + recency), not exponential.
  <https://dl.acm.org/doi/10.1145/3765766.3765803>
- **Forgetful but Faithful (FiFA)** (arXiv:2512.12856, Dec 2025) —
  reflection-based consolidation + sensitivity-aware selection with
  differential-privacy guarantees. Argues learned forgetting should not
  delete safety-critical content. Relevant if tapps-brain ever exposes
  `brain_forget` to untrusted callers. <https://arxiv.org/html/2512.12856v1>
- **Memory-R1 / AgeMem / MemRL (2025)**. Treat store/retrieve/update/
  summarise/discard as callable tools; optimise the policy with RL.
  Learned tactics include proactive mid-session summarisation and
  discarding semantically-redundant records. `arXiv:2508.19828`,
  `arXiv:2601.03192`, `arXiv:2601.01885`.
  *Challenge to tapps-brain:* its deterministic promotion/demotion rules
  are the control baseline these papers beat by 5–15%.

**Implication for tapps-brain.** The half-life-per-tier exponential model
is simple and fast, but psychology has moved past it and 2025 agent papers
are going further — treating forgetting as a learned, sometimes
RL-optimised policy. A follow-up ADR should at least name the Wixted
power-law form and state explicitly that we choose exponential for
computational simplicity, not fit.

### 1.4 Memory consolidation

- **A-MEM (NeurIPS 2025, arXiv:2502.12110)**. Zettelkasten-style
  consolidation: each new memory generates a structured note, the system
  looks for links to historical memories, and accepted links propagate
  updates back to the linked notes. The graph *evolves*, unlike
  tapps-brain's static (project_id, agent_id) partition.
  <https://arxiv.org/abs/2502.12110>
- **Mem0 (arXiv:2504.19413)**. LLM-driven `ADD/UPDATE/DELETE/NOOP` on
  candidate memories derived from dialogue summaries. Reports 26% relative
  gain vs. OpenAI ChatGPT memory on LoCoMo, 91% lower p95 latency than
  full-context, 90% token-cost reduction. **Mem0^g (graph variant)** adds
  ~2% over base.
- **MAGMA: Multi-Graph Agentic Memory** (arXiv:2601.03236, Jan 2026) —
  multi-graph structure (episodic / semantic / procedural subgraphs),
  cross-graph routing on retrieval. `[unverified]` on benchmarks.
- **MEM-α: Learning Memory Construction via RL** (ICLR 2026 MemAgents
  Workshop) — <https://openreview.net/pdf/84b195754f5a425454f70a545ce1e22ee38834db.pdf>.
- **EverMemOS: A Self-Organizing Memory Operating System for Structured
  Memory** (arXiv:2601.02163) — treats the whole memory subsystem as an
  OS abstraction, à la MemGPT. `[unverified depth]`.

### 1.5 Long context vs. external memory

- **Liu et al., *Lost in the Middle*** (TACL 2024, arXiv:2307.03172). The
  foundational paper: LLMs accurately retrieve at the ends of a long
  context but not the middle; degradation is steep, not gradual, even for
  "long-context" models.
- **2025–2026 consensus**. Context caching (Gemini, Anthropic) makes
  "load the corpus once, query many times" cheap when the corpus fits.
  The empirical cutover point cited by multiple 2026 posts is
  ~1M tokens — under that, retrieval often underperforms a
  well-prompted long context for *static* corpora; over that, or when the
  corpus is dynamic, retrieval still wins. Relevant post:
  <https://www.mmntm.net/articles/rag-bifurcation>. No *GA* model offers a
  verified 2M context window as of March 2026.
  *Implication for tapps-brain:* memory is dynamic by definition, so
  retrieval stays. But consider that agents talking to tapps-brain may
  also use context caching for static repo context — we are one side of a
  two-tier system.

### 1.6 Vector-store scaling math (HNSW, quantization)

- **pgvector 0.7.0 (2024 → 2025)**. On `dbpedia-openai-1000k-angular` at
  99% recall, HNSW + binary quantization cut build time ~150× vs. pgvector
  0.5.0; throughput + p99 improved ~30× vs. IVFFlat at equal recall
  (Jonathan Katz, <https://jkatz05.com/post/postgres/pgvector-scalar-binary-quantization/>).
- **Recall-tuning rule of thumb**. `m=16, ef_construction=200` gets you
  high 90s recall on most OpenAI-dimensioned workloads; `m=24,
  ef_construction=200, ef_search=800` pushes to 0.998. Raising any of
  them trades QPS for recall monotonically. Source: Tembo benchmark
  <https://www.tembo.io/blog/vector-indexes-in-pgvector>.
- **Quantization recall**. halfvec (fp16 scalar quantization) keeps
  recall >99% on normalised cosine with OpenAI embeddings; binary
  quantization is production-viable only at 1536-dim+ models and still
  loses recall vs. float. Source: same Katz post.
- **Multi-tenant vector search with RLS + pgvector**. AWS 2024-2025
  reference:
  <https://aws.amazon.com/blogs/database/multi-tenant-vector-search-with-amazon-aurora-postgresql-and-amazon-bedrock-knowledge-bases/>.
  Confirms tapps-brain's ADR-007 / ADR-010 design is the AWS reference
  pattern for RAG tenancy.

## 2. Industry frameworks

### 2.1 Comparison matrix

All columns current as of 2026-03/04 unless noted. "Deploy" = OSS self-hosted
(SH), managed cloud (CL), or both. "LLM in memory path" = does a write/
consolidate/retrieve call the LLM on the hot path?

| System | Storage | Retrieval | Decay/Forget | Consolidation | Isolation | LLM in path | License | Published LoCoMo |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **tapps-brain** (ref) | Postgres-only, tsvector+pgvector HNSW | BM25+vector RRF + rerank, composite score (rel/conf/recency/freq) | Exponential per-tier half-life (180/60/30/14d) | Deterministic text-sim merge | (project_id, agent_id) + Postgres RLS | No | Internal | Not published |
| **Letta** (formerly MemGPT) | Archival + recall tables, pluggable vector/graph backend | Tool-call retrieval; agent self-directs reads | Agent-decided (LLM moves blocks between tiers) | Agent-driven summarisation | Per-agent instance | Yes | Apache-2.0 (SH + CL) | [unverified] |
| **mem0 / Mem0^g** | Vector (default) + optional Neo4j-style graph + KV | Semantic search, threaded concurrent graph+vector | LLM `DELETE` op, implicit via ADD/UPDATE | LLM `ADD/UPDATE/DELETE/NOOP` on dialogue summaries | user/session/agent scopes (app-layer) | Yes (formation + consolidation) | Apache-2.0 (SH + CL) | **91.6** (arXiv:2504.19413, Apr 2025) |
| **Zep / Graphiti** | Temporal knowledge graph (Neo4j backend, Graphiti engine) | Graph traversal + edge-time filtering | Bi-temporal edge invalidation (event time T + ingest time T′) | LLM entity/edge extraction into KG | Per-user graph; cloud tenanting | Yes | Graphiti Apache-2.0; Zep Cloud proprietary | 63.8% on **LongMemEval**, GPT-4o; +15pt over Mem0 on that bench (2025 arXiv:2501.13956) |
| **Cognee** | Multi-store: SQLite/Postgres + LanceDB/pgvector + Kuzu graph | ECL pipeline (Extract/Cognify/Load) + graph + vector | `forget` API operation | `cognify` step builds graph | Per-user DB isolation (2026 roadmap) | Yes | Apache-2.0 (local-first); CL tier | [unverified] |
| **LangGraph memory** | Pluggable checkpointer (Postgres/Redis/SQLite/Mongo/Aerospike) + Store abstraction | Cross-thread memory store + vector search | User-implemented | User-implemented | Thread-scoped short-term, namespace-scoped long-term | Optional | MIT | N/A (primitive, not a memory product) |
| **LlamaIndex memory** | ChatMemoryBuffer / VectorMemory / SummaryBuffer / ComposableMemory | Composable; primary buffer + secondary vector source | Buffer window + LLM summarisation | `ChatSummaryMemoryBuffer` via LLM | None built-in | Yes (for summary buffer) | MIT | N/A |
| **Anthropic Claude memory** (2025-10 → 2026-03) | Anthropic-hosted (opaque) | Context-time injection; user-visible summary regenerated ~24h | Unknown | Periodic LLM summarisation | Per-user account | Yes | Proprietary | N/A |
| **OpenAI ChatGPT memory** | OpenAI-hosted (opaque) | Saved memories + chat-history blend | User can delete individual items | LLM extraction on chat close | Per-user account | Yes | Proprietary | N/A |
| **OpenAI Assistants API** | Threads + files | Automatic per-thread | N/A (bounded thread) | N/A | Per-thread | Yes | Proprietary; **deprecated 2026-08-26** | N/A |
| **Cognition Devin memory** | Proprietary per-VM repo index + DeepWiki | Agent-tool queries | [unverified] | Periodic repo re-indexing (~every few hours) | Per-VM isolation | Yes | Proprietary | N/A |
| **Anthropic Knowledge Graph Memory MCP server** | Local JSON file | Entity/relation/observation CRUD | None | None | Single-file | No | MIT | N/A (reference impl) |
| **Supermemory** | Proprietary | Proprietary | Proprietary | Proprietary | Per-tenant cloud | Yes | Proprietary (CL) | **#1 on LoCoMo & ConvoMem** (self-reported, 2026); 85.4% on LongMemEval |
| **MemMachine** (MemVerge) | Proprietary | Proprietary | Proprietary | Proprietary | Per-tenant cloud | Yes | Proprietary | **91.69 / GPT-4.1-mini** (Sep + Dec 2025 self-reported) |
| **Hindsight** (Vectorize.io) | Proprietary | Proprietary | Proprietary | Proprietary | Per-tenant cloud | Yes | Proprietary | **89.0% OSS-120B / 91.4% Gemini-3 Pro** on LoCoMo (arXiv:2512.12818) |
| **Engram** | Proprietary | Proprietary | Proprietary | Proprietary | [unverified] | Yes | Proprietary | 92% DMR, 80% LoCoMo (self-reported) |

### 2.2 Per-project notes

**Letta (née MemGPT).** The OS-metaphor system: Core Memory (in context =
RAM), Recall Memory (searchable history = disk cache), Archival Memory
(tool-accessed long-term = cold storage). The *agent* moves blocks between
tiers via tool calls — consolidation is emergent, not scheduled. Strength:
survives very long sessions for a single agent. Weakness: assumes one
stateful agent process, which is orthogonal to tapps-brain's 200-
concurrent-agents-per-box design. License Apache-2.0. Docs:
<https://docs.letta.com/advanced/memory-management/>. MemGPT → Letta
rebranding announcement (Oct 2024):
<https://www.letta.com/blog/memgpt-and-letta>.

**mem0.** Closest commercial peer to tapps-brain in terms of being a
"memory layer" rather than an agent framework. Three-tier scoping
(user/session/agent) vs. tapps-brain's two-tier (project_id, agent_id).
Mem0 runs the graph layer *concurrently* with vector writes via
ThreadPoolExecutor — so graph build is async, hot path is vector. Source:
<https://deepwiki.com/mem0ai/mem0/2-core-architecture>. Published 91.6 on
LoCoMo with LLM-as-judge, Apache-2.0.
*Challenge to tapps-brain:* an Apache-2.0 competitor with published SOTA
numbers and a hosted tier. If customers ask "why not mem0," the answer is
tenant isolation (RLS), deterministic latency (no LLM in path), and per-
agent composite keys — not quality.

**Zep / Graphiti.** Graphiti is the OSS core; Zep Cloud wraps it. Key
design is **bi-temporal edges**: every fact records *event time T* (when
the fact became true in the world) and *ingest time T′* (when the system
learned about it). This supports retroactive corrections and supersession
— something tapps-brain would have to simulate with explicit
`superseded_by` columns. P95 retrieval latency reported at 300ms. Paper:
<https://arxiv.org/abs/2501.13956>. Graphiti MCP server exists as an
official adapter.
*Contradiction to tapps-brain:* if temporal correctness is a customer
requirement, tapps-brain has no first-class answer. Current design
treats a memory as immutable+decaying; it doesn't distinguish "this fact
was true until 2026-02" from "confidence decayed below threshold."

**Cognee.** Only framework in this list with a 2026 roadmap to ship a
**Rust on-device engine** and explicit per-user DB isolation. Local-first
stance (SQLite + LanceDB + Kuzu by default, no external services). API
surface is four verbs: `remember / recall / forget / improve`. Raised
$7.5M seed, reported 500× pipeline-run growth in 2025. Source:
<https://www.cognee.ai/blog/cognee-news/cognee-raises-seven-million-five-hundred-thousand-dollars-seed>.

**LangGraph memory.** Not a memory *system* — a primitive layer with two
concepts: **Checkpointers** (thread-scoped short-term, one per graph
step) and **Stores** (cross-thread long-term). 2025–2026 saw official
drivers for Postgres, Redis, MongoDB, Aerospike. Docs:
<https://docs.langchain.com/oss/python/langgraph/add-memory>.
*Implication for tapps-brain:* the LangGraph `Store` interface is a
reasonable target for an adapter if we want to be first-class inside
LangGraph without them having to know about MCP.

**LlamaIndex memory.** Four modules:
`ChatMemoryBuffer`, `VectorMemory`, `ChatSummaryMemoryBuffer`,
`SimpleComposableMemory`. Composable — the common pattern is a primary
buffer + a secondary vector source whose hits are injected into the
system prompt. Not a persistence story — it's a conversation-state
story. Docs:
<https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/>.

**Anthropic Claude memory.** Shipped to paid users 2025-10-23; to free
tier 2026-03-02. Two mechanisms: (a) periodic LLM-generated summary of
user facts (regenerated ~every 24h), (b) explicit "remember X" via a
dedicated tool that updates the summary immediately. Opaque backend.
Source:
<https://www.macrumors.com/2025/10/23/anthropic-automatic-memory-claude/>,
<https://www.macrumors.com/2026/03/02/anthropic-memory-import-tool/>.
*Implication for tapps-brain:* Claude's native memory is per-user at the
chat level, not per-agent-in-a-codebase. tapps-brain's niche (persistent
memory scoped to a *project* + *coding agent*, independent of which
chatbot is driving) is not addressed by Anthropic's feature.

**OpenAI ChatGPT memory + Assistants deprecation.** ChatGPT memory
splits into "Saved Memories" (explicit) and "Chat History" (scanned).
The Assistants API — previously the canonical "LLM + memory + tools"
abstraction — is **deprecated 2026-08-26**; replacement is the Responses
+ Conversations API pair. Every `/v1/assistants`, `/v1/threads`,
`/v1/threads/runs` call will error after that date. Source:
<https://clonepartner.com/blog/openai-assistants-api-shutdown-the-2026-migration-guide>.
*Implication for tapps-brain:* any customer integration that wrapped
Assistants for memory has to migrate in 2026. Small opportunity window
for "bring your own memory layer, keep your existing OpenAI calls."

**Cognition Devin.** Public info is thin on internals. What is known:
parallel VM-isolated Devin instances; automatic repo re-indexing every
few hours; DeepWiki for codebase summaries. Memory architecture details
are not published.
<https://cognition.ai/blog/devin-2>.

**Anthropic Knowledge Graph Memory MCP server.** Published 2025-02-25,
MIT-licensed reference implementation under
`modelcontextprotocol/servers/tree/main/src/memory`. Stores
entity/relation/observation triples in a **single local JSON file**.
Explicitly a *reference* — no scaling, no concurrency story.
*Implication for tapps-brain:* tapps-brain is in a different product
category. The reference impl is what a user graduates *out of* toward
tapps-brain / Zep / mem0.

**Supermemory, MemMachine, Hindsight, Engram.** Cloud-only proprietary
systems competing on LoCoMo / LongMemEval scores. All publish leaderboard-
topping numbers; none publish architecture. Two things to note:
(a) *everyone* reports numbers with a GPT-4-class judge, so the absolute
numbers are LLM-judge-anchored, not objective; (b) the top systems
cluster at 85–92%, which is close to LoCoMo's human ceiling of 87.9.

### 2.3 Licenses — the commercial landscape

Apache-2.0 is the dominant license for OSS memory systems: **mem0, Letta,
Graphiti (core), Cognee**. MIT for **LlamaIndex, LangGraph, MCP reference
servers**. Proprietary for everything hosted as a service
(Claude/ChatGPT/Assistants, Zep Cloud, Supermemory, MemMachine, Engram,
Hindsight, Devin). There is no AGPL or SSPL player in this space as of
April 2026, which is notable — compare to database-land where Redis and
MongoDB both re-licensed.

## 3. Standards and protocols

### 3.1 MCP status April 2026

- **Spec.** Current active spec version is **2025-11-25**
  (<https://modelcontextprotocol.io/specification/2025-11-25>). 2026 roadmap
  post published by the MCP maintainers:
  <https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/>.
- **Adoption numbers** (Mar 2026, Anthropic-reported via The New Stack):
  ~10,000 active public MCP servers; 97M monthly SDK downloads across
  Python + TypeScript. <https://thenewstack.io/model-context-protocol-roadmap-2026/>
- **Server Cards (v2.1, 2026).** Standardised `.well-known` URL exposing
  structured server metadata so registries/crawlers can discover
  capabilities without connecting.
- **Transport scalability.** The current Streamable HTTP transport is the
  focus of 2026 work — stateful sessions fight load balancers, horizontal
  scaling is ad-hoc. Directly relevant to tapps-brain's dual-port model
  (8080 data+MCP, 8090 operator MCP).
- **No memory-portability standard.** There is currently **no** W3C/IETF
  or MCP-side spec for "memory objects" that could be exchanged between
  memory providers. This is discussed as an open problem at MemAgents
  (ICLR 2026) but not yet proposed. `[unverified: absence]` — confirmed
  by absence across MCP spec, W3C AI CG, IETF AI-related WGs searched.

### 3.2 Implication for tapps-brain

Being MCP-native is aligned; the 55-tool surface plus Streamable HTTP
matches where the ecosystem is going. The 8090 operator split pre-empts
one of the known scaling issues. The *absence* of a memory-portability
standard means tapps-brain has no "export to standard format" obligation
yet, but an export story (JSON-LD? `mem0`-compatible JSON?) would be cheap
insurance.

## 4. Talks and discussions (2025–2026)

- **AI Engineer Summit NYC, Feb 2025** — Agent Memory Systems was a named
  technical track on Day 2 (Agents). Talks are on the AI Engineer YouTube
  channel (260k+ subs). <https://www.ai.engineer/summit/2025>
- **Latent Space — Harrison Chase (LangChain)** on "harness vs.
  framework" and memory as part of the harness:
  <https://www.latent.space/p/langchain>. Sequoia podcast, *Context
  Engineering Our Way to Long-Horizon Agents*, same guest:
  <https://sequoiacap.com/podcast/context-engineering-our-way-to-long-horizon-agents-langchains-harrison-chase/>.
- **Dwarkesh Patel podcast, Dec 2025 — *Thoughts on AI progress***
  (<https://www.dwarkesh.com/p/thoughts-on-ai-progress-dec-2025>). Takeaway
  relevant here: "more bullish on memory than on fine-tuning"; scratchpad/
  filesystem-as-memory pattern as a transitional architecture.
- **NeurIPS 2025 poster — A-Mem** —
  <https://neurips.cc/virtual/2025/poster/119020>.
- **ICLR 2026 MemAgents Workshop** — program & accepted papers at
  <https://iclr.cc/virtual/2026/workshop/10000792>. Most concentrated
  source of 2026 memory-specific research.
- **Memgraph / Cognee webinar series** — vendor content but technical;
  Memgraph + Cognee integration details at
  <https://memgraph.com/blog/from-rag-to-graphs-cognee-ai-memory>.
- **Cole Medin — *I Built Self-Evolving Claude Code Memory w/ Karpathy's
  LLM Knowledge Bases*** (YouTube, 2026-04) —
  <https://youtu.be/7huCP6RkcY4>. Launch walkthrough and design
  rationale for the `claude-memory-compiler` repo
  (<https://github.com/coleam00/claude-memory-compiler>, published the
  same day, 2026-04-06): Claude Code hooks capture session transcripts,
  a background `flush.py` calls the Claude Agent SDK to extract a
  daily log, and `compile.py` LLM-compiles daily logs into
  cross-referenced markdown concept / connection / Q&A articles with
  provenance. Relevance to tapps-brain: a point-by-point contrast —
  Cole's system is single-user, local-markdown, no-LICENSE, no-MCP,
  no-decay, LLM-on-write; tapps-brain is multi-tenant, Postgres,
  MIT, 55-tool MCP, exponential decay per tier, deterministic-on-write.
  The Karpathy "LLM reads a structured index, skip the embeddings" thesis
  (50–500 articles, personal scale) is the useful design frame — it
  clarifies *why* tapps-brain's BM25 + pgvector + RRF stack is the right
  answer at fleet scale (20+ agents × N projects) even though it would
  be over-engineered for Cole's use case. [transcript unavailable
  2026-04-17] — summary derived from title, repo README, AGENTS.md, and
  the franksworld.com writeup
  (<https://www.franksworld.com/2026/04/06/how-to-build-a-self-evolving-ai-memory-with-karpathys-llm-knowledge-bases/>).
  Scored in the companion scorecard at 32.2/100 (rank 16/16) —
  see `memory-systems-scorecard.md` §"claude-memory-compiler".

## 5. Benchmarks and evaluation

### 5.1 Benchmarks in current use

| Benchmark | What it measures | Who published | Year | Notes |
| --- | --- | --- | --- | --- |
| **LoCoMo** | Long-term conversational memory across weeks/months; single-hop, multi-hop, temporal, open-domain, adversarial QA | Maharana et al. (Snap) | 2024, arXiv:2402.17753 | Now the *de facto* benchmark. 32 sessions, ~600 turns, ~16k tokens, multi-modal. Human ceiling F1 87.9. Open-LLM baselines 13.9–32.1 F1; RAG adds 22–66% but still gaps humans by ~56%. |
| **LongMemEval** (ICLR 2025) | 5 abilities: info extraction, multi-session reasoning, temporal reasoning, knowledge updates, abstention | Xiao et al. (arXiv:2410.10813) | ICLR 2025 | 500 questions. Zep scored 63.8% on GPT-4o; Supermemory 85.4%. |
| **MemoryAgentBench** | Incremental multi-turn memory eval; 4 competencies (accurate retrieval, test-time learning, long-range understanding, selective forgetting) | arXiv:2507.05257 | ICLR 2026 | Framework + dataset. |
| **MemBench / MemoryBench** | Memory effectiveness, efficiency, capacity in agents | ACL 2025 findings + arXiv:2510.17281 | 2025 | MemoryBench also tests continual learning from feedback; finds forgetting under feedback is widespread. |
| **LoCoMo-Plus** (arXiv:2602.10715) | Cognitive memory under cue-trigger semantic disconnect | 2026 | Extension of LoCoMo to latent-constraint retention. |
| **DMR (Deep Memory Retrieval)** | Cross-session fact retrieval | MemGPT paper | 2023–2024 | Older; Zep's headline improvement was against DMR. |
| **BEIR 2.0** | General IR across 17+ heterogeneous tasks | beir-cellar | 2025 | nDCG@10 standard. Foundational, not agent-specific. |
| **MTEB** | Text-embedding quality across 58 datasets, 112 languages, 8 tasks | HuggingFace | ongoing | Incorporates BEIR. Used for picking the embedding model, not the memory system. |
| **RAGAS** (arXiv:2309.15217) | Reference-free RAG eval: faithfulness, answer relevance, context precision/recall | 2023 → evolved | — | Most operators use RAGAS for *their* pipeline, not as a cross-system benchmark. |
| **ConvoMem** | Conversational memory, similar scope to LoCoMo | Supermemory-maintained | 2025 | Supermemory reports #1. |
| **Agent Memory Benchmark (AMB)** | Memory across tool calls, preferences in multi-step decisions, doc-research knowledge | Vectorize/Hindsight | 2026-03 | Manifesto at <https://hindsight.vectorize.io/blog/2026/03/23/agent-memory-benchmark>. |

### 5.2 Scoreboard (LoCoMo, as published)

| System | Score | Model / judge | Source |
| --- | --- | --- | --- |
| Hindsight | 89.0 | OSS-120B | arXiv:2512.12818 |
| Hindsight | **91.4** | Gemini-3 Pro | arXiv:2512.12818 |
| MemMachine v0.2 | **91.69** | GPT-4.1-mini | memmachine.ai blog, Dec 2025 |
| Mem0 | 91.6 | LLM-as-judge (GPT-4 class) | arXiv:2504.19413 |
| Supermemory | #1 (exact score gated) | — | supermemory.ai/research |
| Engram | 80 | — | engram.fyi/research (self-reported) |
| Human ceiling | 87.9 F1 | — | arXiv:2402.17753 |
| GPT-4 / Llama-2-70B / GPT-3.5 / Mistral-7B baselines | 13.9–32.1 F1 | — | arXiv:2402.17753 |

Every published top-of-leaderboard result uses a GPT-4-class judge; the
numbers should be read as "relative to other systems using the same
judge" not as absolute accuracies.

### 5.3 Latency / pricing data

- **Zep / Graphiti** — self-reported P95 300ms for graph retrieval. arXiv:2501.13956.
- **Mem0** — self-reported 91% p95 latency reduction vs. full-context
  baseline; 90% token-cost reduction. arXiv:2504.19413.
- **pgvector HNSW at 1M vectors** — <20ms query, recall >95% with default
  HNSW parameters (Instaclustr benchmark):
  <https://www.instaclustr.com/education/vector-database/pgvector-performance-benchmark-results-and-5-ways-to-boost-performance/>.
- **Gemini context cache pricing** — $0.125 / 1M cached input tokens vs.
  $1.25 / 1M standard input; min cached segment 32,768 tokens; stable on
  1.5 Pro / Flash. 2M-window GA across providers **not** yet verified
  as of March 2026.
- **Vector DB pricing** — deliberately not surveyed here; pgvector-on-
  owned-Postgres is effectively free marginal cost for tapps-brain, so
  comparisons to managed Pinecone/Qdrant/Weaviate prices are not
  decision-relevant.

### 5.4 What tapps-brain doesn't measure yet

- No published LoCoMo or LongMemEval number.
- No public latency benchmark at 200 concurrent agents.
- No recall@k sweep on the pgvector HNSW config we actually ship.
- No feedback-loop efficacy number from the quality flywheel.

## Open questions for tapps-brain

1. **Power-law vs. exponential decay.** Should the decay model change?
   Cheap experiment: add a `decay_model` column with `exponential|power_law`
   and A/B the two on a synthetic LoCoMo-style trace.
2. **Graph layer as optional write-side.** Every SOTA 2025–2026 system
   has *some* entity/relation layer. Adding an `entities` + `relations`
   table behind a feature flag (no change to default retrieval path)
   is cheap and hedges against "Zep/Graphiti wins" becoming the durable
   story.
3. **LLM in the consolidation path?** tapps-brain's deterministic merge
   is a differentiator (latency, determinism, 200-agent concurrency).
   But Mem0 and A-MEM beat it on LoCoMo because they use an LLM for
   formation and consolidation. Is there a batched/async path that keeps
   the *hot* read/write deterministic but runs an LLM-driven `CONSOLIDATE`
   pass out-of-band?
4. **Bi-temporal facts.** Do customers need to express "this fact was
   true until date X"? If yes, tapps-brain's current (timestamp,
   last_access, decay) triple is insufficient; Graphiti's event-time +
   ingest-time pair would be needed.
5. **Publish a LoCoMo number.** The pure-deterministic path will likely
   score lower than LLM-assisted systems. But *any* published number is
   more defensible than "we didn't measure" when a customer asks.
6. **Memory export contract.** No standard exists, so there is no
   ecosystem pressure. But a `brain_export` → `mem0-compatible` +
   `jsonld` dual output would pre-empt the "am I locked in?" objection.
7. **Position vs. Claude's and ChatGPT's native memory.** Both are
   per-user at the chat level. tapps-brain is per-(project_id, agent_id).
   The pitch is: *cross-chat, cross-vendor, project-scoped* memory. This
   should be made explicit in the README and server.json description —
   the current wording does not frontload the distinction.
8. **Assistants API sunset (2026-08-26) opportunity.** Any customer
   rebuilding on Responses + Conversations needs to re-home their memory.
   Publishing an Assistants→tapps-brain migration guide in Q2 2026
   captures that window.

## Source index

- <https://arxiv.org/abs/2603.07670> — Memory for Autonomous LLM Agents: Mechanisms, Evaluation, and Emerging Frontiers (2026)
- <https://arxiv.org/abs/2512.13564> — Memory in the Age of AI Agents (Dec 2025)
- <https://github.com/Shichun-Liu/Agent-Memory-Paper-List> — curated paper list (2026)
- <https://dl.acm.org/doi/10.1145/3748302> — Survey on Memory Mechanism of LLM Agents, ACM TOIS (Jul 2025)
- <https://sites.google.com/view/memagent-iclr26/> — ICLR 2026 MemAgents Workshop
- <https://iclr.cc/virtual/2026/workshop/10000792> — ICLR 2026 MemAgents program
- <https://openreview.net/pdf?id=U51WxL382H> — MemAgents workshop proposal (2026)
- <https://openreview.net/pdf/84b195754f5a425454f70a545ce1e22ee38834db.pdf> — MEM-α (ICLR 2026 MemAgents)
- <https://arxiv.org/abs/2405.14831> — HippoRAG (NeurIPS 2024)
- <https://github.com/OSU-NLP-Group/HippoRAG> — HippoRAG 2 code / ICML 2025
- <https://dl.acm.org/doi/10.1145/3596512> — Analysis of Fusion Functions for Hybrid Retrieval, ACM TOIS 2023
- <https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/> — OpenSearch 2.19 RRF (2024-10)
- <http://wixtedlab.ucsd.edu/publications/wixted/Wixted_and_Carpenter_(2007).pdf> — Wickelgren power law / Ebbinghaus savings
- <http://wixtedlab.ucsd.edu/publications/wixted/Wixted_and_Ebbesen_(1997).pdf> — Genuine power curves in forgetting
- <https://memory.psych.upenn.edu/files/pubs/KahaAdle02.pdf> — Kahana & Adler, Note on the Power Law of Forgetting
- <https://dl.acm.org/doi/10.1145/3765766.3765803> — Human-Like Remembering and Forgetting in LLM Agents (ACT-R), HAI 2025
- <https://arxiv.org/html/2512.12856v1> — Forgetful but Faithful (FiFA), Dec 2025
- <https://arxiv.org/pdf/2508.19828> — Memory-R1 (2025)
- <https://arxiv.org/html/2601.03192v2> — MemRL: Self-Evolving Agents via Runtime RL on Episodic Memory (2026)
- <https://arxiv.org/html/2601.01885v1> — Agentic Memory: Unified Long/Short-Term Memory Management (2026)
- <https://arxiv.org/abs/2502.12110> — A-MEM (NeurIPS 2025)
- <https://neurips.cc/virtual/2025/poster/119020> — A-Mem poster
- <https://arxiv.org/abs/2504.19413> — Mem0 paper (Apr 2025)
- <https://huggingface.co/papers/2504.19413> — Mem0 on HF Papers
- <https://arxiv.org/html/2601.03236v1> — MAGMA: Multi-Graph Agentic Memory (Jan 2026)
- <https://arxiv.org/pdf/2601.02163> — EverMemOS (2026)
- <https://arxiv.org/abs/2307.03172> — Lost in the Middle (TACL 2024)
- <https://aclanthology.org/2024.tacl-1.9/> — Lost in the Middle published
- <https://www.mmntm.net/articles/rag-bifurcation> — The Death of Standard RAG (2026)
- <https://ragflow.io/blog/rag-review-2025-from-rag-to-context> — RAG to Context (2025)
- <https://jkatz05.com/post/postgres/pgvector-scalar-binary-quantization/> — pgvector scalar/binary quantization benchmarks (2025)
- <https://aws.amazon.com/blogs/database/accelerate-hnsw-indexing-and-searching-with-pgvector-on-amazon-aurora-postgresql-compatible-edition-and-amazon-rds-for-postgresql/> — pgvector 0.7.0 on Aurora (2025)
- <https://www.tembo.io/blog/vector-indexes-in-pgvector> — pgvector HNSW parameter tuning
- <https://www.instaclustr.com/education/vector-database/pgvector-performance-benchmark-results-and-5-ways-to-boost-performance/> — pgvector perf benchmark
- <https://aws.amazon.com/blogs/database/multi-tenant-vector-search-with-amazon-aurora-postgresql-and-amazon-bedrock-knowledge-bases/> — AWS multi-tenant pgvector reference
- <https://aws.amazon.com/blogs/database/self-managed-multi-tenant-vector-search-with-amazon-aurora-postgresql/> — AWS self-managed multi-tenant
- <https://www.tigerdata.com/blog/building-multi-tenant-rag-applications-with-postgresql-choosing-the-right-approach> — Multi-tenant RAG on Postgres
- <https://medium.com/@michael.hannecke/implementing-row-level-security-in-vector-dbs-for-rag-applications-fdbccb63d464> — RLS in vector DBs
- <https://github.com/letta-ai/letta> — Letta OSS
- <https://docs.letta.com/concepts/memgpt/> — MemGPT concepts (Letta)
- <https://docs.letta.com/advanced/memory-management/> — Letta memory mgmt
- <https://www.letta.com/blog/memgpt-and-letta> — MemGPT → Letta transition
- <https://github.com/mem0ai/mem0> — mem0 OSS
- <https://deepwiki.com/mem0ai/mem0> — mem0 DeepWiki
- <https://deepwiki.com/mem0ai/mem0/2-core-architecture> — mem0 core architecture
- <https://deepwiki.com/mem0ai/mem0/4-graph-memory> — mem0 graph memory
- <https://docs.mem0.ai/cookbooks/essentials/choosing-memory-architecture-vector-vs-graph> — mem0 vector vs graph
- <https://mem0.ai/blog/graph-memory-solutions-ai-agents> — Graph memory comparison (Jan 2026)
- <https://mem0.ai/research> — mem0 token-efficient memory algorithm post
- <https://arxiv.org/abs/2501.13956> — Zep: Temporal Knowledge Graph Architecture for Agent Memory (Jan 2025)
- <https://blog.getzep.com/content/files/2025/01/ZEP__USING_KNOWLEDGE_GRAPHS_TO_POWER_LLM_AGENT_MEMORY_2025011700.pdf> — Zep KG paper PDF
- <https://github.com/getzep/graphiti> — Graphiti OSS
- <https://neo4j.com/blog/developer/graphiti-knowledge-graph-memory/> — Graphiti on Neo4j blog
- <https://github.com/topoteretes/cognee> — Cognee OSS
- <https://www.cognee.ai/blog/fundamentals/how-cognee-builds-ai-memory> — Cognee architecture
- <https://www.cognee.ai/blog/cognee-news/cognee-raises-seven-million-five-hundred-thousand-dollars-seed> — Cognee funding (2026)
- <https://www.cognee.ai/blog/cognee-news/introducing-cognee-mcp> — Cognee MCP
- <https://memgraph.com/blog/from-rag-to-graphs-cognee-ai-memory> — Cognee / Memgraph integration
- <https://docs.langchain.com/oss/python/langgraph/add-memory> — LangGraph memory docs
- <https://www.mongodb.com/company/blog/product-release-announcements/powering-long-term-memory-for-agents-langgraph> — LangGraph + MongoDB
- <https://redis.io/blog/langgraph-redis-build-smarter-ai-agents-with-memory-persistence/> — LangGraph + Redis
- <https://itbusinessnet.com/2026/03/aerospike-nosql-database-8-delivers-durable-and-low-latency-memory-store-for-langgraph-agentic-ai-workflows/> — Aerospike 8 + LangGraph (Mar 2026)
- <https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/> — LlamaIndex memory modules
- <https://developers.llamaindex.ai/python/examples/agent/memory/composable_memory/> — SimpleComposableMemory
- <https://support.claude.com/en/articles/12138966-release-notes> — Claude release notes
- <https://www.macrumors.com/2025/10/23/anthropic-automatic-memory-claude/> — Claude automatic memory (Oct 2025)
- <https://www.macrumors.com/2026/03/02/anthropic-memory-import-tool/> — Claude memory to free tier (Mar 2026)
- <https://xtrace.ai/blog/claude-memory-2026-limits-and-fixes> — Claude memory 2026 analysis
- <https://help.openai.com/en/articles/8983136-what-is-memory> — OpenAI "What is Memory"
- <https://learn.microsoft.com/en-us/answers/questions/5571874/openai-assistants-api-will-be-deprecated-in-august> — Assistants API deprecation (Aug 2026)
- <https://clonepartner.com/blog/openai-assistants-api-shutdown-the-2026-migration-guide> — Assistants API migration guide
- <https://cognition.ai/blog/devin-2> — Devin 2.0
- <https://medium.com/@takafumi.endo/agent-native-development-a-deep-dive-into-devin-2-0s-technical-design-3451587d23c0> — Devin 2.0 technical design
- <https://github.com/modelcontextprotocol/servers/tree/main/src/memory> — Anthropic KG Memory MCP server
- <https://www.pulsemcp.com/servers/modelcontextprotocol-memory> — MCP memory server catalog entry
- <https://modelcontextprotocol.io/specification/2025-11-25> — MCP spec 2025-11-25
- <https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/> — 2026 MCP roadmap
- <https://thenewstack.io/model-context-protocol-roadmap-2026/> — The New Stack on MCP growing pains (2026)
- <https://en.wikipedia.org/wiki/Model_Context_Protocol> — MCP Wikipedia
- <https://ai.google.dev/gemini-api/docs/long-context> — Gemini long context docs
- <https://www.aifreeapi.com/en/posts/gemini-api-context-caching-reduce-cost> — Gemini context caching guide (2026)
- <https://medium.com/google-cloud/skip-the-rag-workflows-with-geminis-2m-context-window-and-the-context-cache-d9345730e3c0> — Gemini 2M + cache
- <https://snap-research.github.io/locomo/> — LoCoMo site
- <https://arxiv.org/abs/2402.17753> — LoCoMo paper
- <https://github.com/snap-research/locomo> — LoCoMo code
- <https://arxiv.org/html/2602.10715v1> — LoCoMo-Plus
- <https://openreview.net/forum?id=pZiyCaVuti> — LongMemEval ICLR 2025
- <https://github.com/xiaowu0162/longmemeval> — LongMemEval code
- <https://arxiv.org/abs/2507.05257> — MemoryAgentBench (ICLR 2026)
- <https://github.com/HUST-AI-HYZ/MemoryAgentBench> — MemoryAgentBench code
- <https://aclanthology.org/2025.findings-acl.989/> — MemBench (ACL 2025 Findings)
- <https://arxiv.org/html/2510.17281v4> — MemoryBench
- <https://hindsight.vectorize.io/blog/2026/03/23/agent-memory-benchmark> — Agent Memory Benchmark manifesto (Mar 2026)
- <https://arxiv.org/html/2512.12818v1> — Hindsight is 20/20 (Dec 2025)
- <https://supermemory.ai/research/> — Supermemory research
- <https://github.com/supermemoryai/memorybench> — memorybench unified eval
- <https://memmachine.ai/blog/2025/09/memmachine-reaches-new-heights-on-locomo/> — MemMachine on LoCoMo (Sep 2025)
- <https://memmachine.ai/blog/2025/12/memmachine-v0.2-delivers-top-scores-and-efficiency-on-locomo-benchmark/> — MemMachine v0.2 (Dec 2025)
- <https://arxiv.org/html/2604.04853> — MemMachine paper
- <https://www.engram.fyi/research> — Engram benchmarks (self-reported)
- <https://arxiv.org/abs/2309.15217> — RAGAS
- <https://github.com/beir-cellar/beir> — BEIR
- <https://app.ailog.fr/en/blog/news/beir-benchmark-update> — BEIR 2.0 leaderboard (2025)
- <https://arxiv.org/pdf/2506.21182> — Maintaining MTEB
- <https://arxiv.org/html/2604.04514v1> — SuperLocalMemory V3.3 (2026)
- <https://www.ai.engineer/summit/2025> — AI Engineer Summit NYC 2025
- <https://www.latent.space/p/langchain> — Latent Space: Harrison Chase
- <https://sequoiacap.com/podcast/context-engineering-our-way-to-long-horizon-agents-langchains-harrison-chase/> — Sequoia podcast: LangChain, long-horizon agents
- <https://www.dwarkesh.com/p/thoughts-on-ai-progress-dec-2025> — Dwarkesh, Thoughts on AI progress (Dec 2025)
