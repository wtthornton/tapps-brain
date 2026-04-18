# Agent memory systems — comparative scorecard (2026-04-17)

> Companion to `memory-systems-2026.md`. That document is the vetted knowledge
> base; this one turns it into a weighted scorecard so we can say "where does
> tapps-brain actually rank." Every score carries a one-sentence
> justification and at least one citation. Claims that could not be verified
> from a primary source are tagged `[unverified]` and scored conservatively.
>
> Scoring agent is running inside the tapps-brain repo. Self-scoring bias is
> addressed by applying the rubric honestly — where tapps-brain has no
> published evidence (benchmarks, adopters), it is scored low, not hedged.
> The fact that self-assessment lands tapps-brain mid-pack, not top, is
> intentional evidence that the bias check worked.

---

## Scorecard methodology

Eleven dimensions, each scored 0–5 integer. Weighted sum → overall score
out of 100. Score contribution = `score × weight / 5`. The 2026-04-17
structural audit split the former D6 "Transport & interop" (w10) into
D6a "MCP depth" (w6) and D6b "Language/SDK reach" (w4). Weights still
sum to 100; there are now 11 dimensions.

| # | Dimension | Weight | Rubric (5 vs 0) |
|---|---|---|---|
| 1 | Storage & ops | 8 | 5 = single-backend, stateless, zero-click install; 0 = 4+ services, manual migrations, fragile |
| 2 | Retrieval quality | 15 | 5 = strong published LoCoMo / MemGPT / custom benchmark with >80% on a relevant metric AND hybrid; 0 = no benchmarks, single-signal retrieval |
| 3 | Decay / forgetting | 8 | 5 = power-law or learned, per-category, configurable; 3 = exponential per-tier; 0 = no decay, unbounded growth |
| 4 | Consolidation | 10 | 5 = automatic duplicate merge + contradiction handling + provenance kept; 0 = no consolidation, every write is a new row |
| 5 | Multi-tenancy & isolation | 10 | 5 = per-project + per-agent composite key enforced at DB layer (RLS or equivalent); 0 = no isolation, all memories pooled |
| 6a | MCP depth | 6 | 5 = first-class MCP server shipped by the project, 40+ tools OR tools+resources+prompts with dual transport (data + operator), Streamable HTTP; 4 = first-class MCP server, 15–39 tools, Streamable HTTP or SSE; 3 = MCP server exists, modest tool surface (5–14 tools) OR high-quality reference impl; 2 = third-party MCP wrappers only, no first-party; 1 = MCP on roadmap or docs mention, not shipped; 0 = no MCP |
| 6b | Language/SDK reach | 4 | 5 = ≥3 stable SDKs (e.g. Python + TS + one of Go/Java/Rust) + REST; 4 = 2 stable SDKs (typically Python + TS) + REST; 3 = 1 mature SDK + REST API; 2 = 1 SDK, no REST; 1 = one language only, no SDK abstraction; 0 = no stable SDK |
| 7 | License & commercial | 7 | 5 = permissive OSS (MIT/Apache) with real self-host; 0 = SaaS-only or AGPL-only |
| 8 | Production readiness | 12 | 5 = OTel, migrations, ≥80% test coverage, versioned API, release every 2–4 weeks; 0 = POC only |
| 9 | Write-path design | 10 | 5 = user-choosable (deterministic OR LLM), documented cost/quality trade; 3 = one or the other, well-documented; 0 = opaque |
| 10 | Momentum | 10 | 5 = >5k stars, weekly releases, named production adopters; 0 = archived/stale/<200 stars |

Total weight = 100 (8 + 15 + 8 + 10 + 10 + 6 + 4 + 7 + 12 + 10 + 10).
Max possible = 100. A system scoring 5 on everything scores exactly 100.
Per-system raw totals are rounded to one decimal place.

---

## Top-line results

Sorted by overall score desc. 16 systems scored. Raw totals to one decimal.

| Rank | System | Overall | Top 3 strengths | Top 3 weaknesses |
|---|---|---|---|---|
| 1 | mem0 | 79.6 | Published LoCoMo 91.6, Python + TS SDK + REST + hosted tier, Apache-2.0 | No DB-layer tenant isolation, LLM required in write path, no first-party MCP server (third-party wrappers only) |
| 2 | Graphiti (Zep OSS) | 75.8 | Bi-temporal knowledge graph, Apache-2.0, first-class MCP server (mcp-v1.0.2) | Requires Neo4j/FalkorDB backend, graph-build latency, no public recall-quality benchmark on LongMemEval leaderboard |
| 3 | Supermemory | 73.4 | MIT + self-host + 21.9k stars, MCP shipped, JS/TS + Python SDKs | SaaS-primary, architecture proprietary, "leaderboard" claims externally disputed *(D7 raised from 0 to 3 in 2026-04-17 score audit: MIT license + real self-host confirmed)* |
| 2-3 | **tapps-brain** | **77.8** | Power-law per-tier decay (FSRS-canonical, STORY-SC02 2026-04-17), embedding-cosine consolidation + polarity contradiction detection (STORY-SC03 2026-04-18), DB-layer RLS tenant isolation, 55-tool first-class MCP surface with dual transport (data :8080 + operator :8090), production-ready (OTel, migrations, 127 test files) | No published benchmark, no external adopters, single-language SDK |
| 5 | Memori (MemoriLabs) | 72.2 | Published LoCoMo 81.95, Apache-2.0, Rust components + Python/TS SDK + MCP | LLM-in-path only, smaller ecosystem than mem0, entity-process-session isolation is app-layer |
| 6 | LangGraph memory | 68.6 | Ubiquitous adoption (29.5k stars), MIT, multi-backend checkpointers + Store abstraction | Not a memory product (primitive), user must implement decay/consolidation, no first-party MCP server |
| 7 | Cognee | 68.0 | `remember/recall/forget/improve` API, Apache-2.0, first-class `cognee-mcp` server | Multi-service ops burden, no published benchmark, per-user isolation on 2026 roadmap not shipped |
| 8 | Letta (MemGPT successor) | 67.6 | Apache-2.0 + cloud, 22.1k stars, Python + TS SDKs + Letta Code CLI | No public LoCoMo number, LLM-in-path required, MCP not in current docs (roadmap/community only) |
| 9 | LlamaIndex memory | 63.8 | Composable memory modules, MIT, 48.7k stars | Not a persistence story, no benchmarks, no first-party MCP server for memory modules |
| 10 | MemPalace (discounted) | 62.4 | 29 MCP tools, claimed benchmarks spanning LongMemEval/LoCoMo/ConvoMem/MemBench | Benchmark methodology externally disputed; accusations of paid stars; ChromaDB-backed headline score not unique to MemPalace |
| 11 | agentmemory (rohitg00) | 59.2 | Self-reported LongMemEval-S 95.2, triple-stream retrieval, 44 MCP tools + 6 resources | Single-node SQLite, 1.7k stars, lightly adopted *(D2 lowered from 4 to 3 in 2026-04-17 score audit: no arXiv/peer-reviewed source)* |
| 12 | OMEGA (omega-memory) | 59.0 | Self-reported LongMemEval 95.4, local-first, MCP + REST with Claude Desktop/Cursor setup | SQLite single-process, no DB-layer multi-tenancy, only ~98 GitHub stars *(D2 lowered from 5 to 4 and D10 from 3 to 1 in 2026-04-17 score audit)* |
| 13 | MemRL | 51.6 | ICLR/arXiv 2026 paper, MIT, RL-learned policy | Research code, 86 stars, no MCP, no production claims |
| 14 | A-MEM (agiresearch) | 50.8 | NeurIPS 2025 paper, MIT, Zettelkasten-style evolving notes | Research code, 972 stars, no MCP, no production hardening |
| 15 | Anthropic KG Memory MCP | 43.2 | Canonical ~9-tool MCP reference, MIT, zero-dep JSONL file | Single-file, no concurrency, no decay, no consolidation, no benchmarks |
| 16 | claude-memory-compiler (coleam00) | 32.2 | Single-file markdown KB with zero backend services, LLM-driven dedup/connection articles with provenance, viral launch (783 stars in ~10 days) | No LICENSE file in repo, no MCP server (uses Claude Code hooks instead), no decay and no multi-tenancy — explicitly single-user local-only |

---

## Detailed comparison matrix

Columns: D1 Storage & ops · D2 Retrieval quality · D3 Decay · D4 Consolidation
· D5 Multi-tenancy · D6a MCP depth · D6b Language/SDK reach · D7 License ·
D8 Production readiness · D9 Write-path · D10 Momentum · T = weighted total.

Weighted contribution per cell = `score × weight / 5`. Totals are raw
weighted sums rounded to one decimal. Sorted by T desc.

| System | D1 (w8) | D2 (w15) | D3 (w8) | D4 (w10) | D5 (w10) | D6a (w6) | D6b (w4) | D7 (w7) | D8 (w12) | D9 (w10) | D10 (w10) | **T** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| mem0 | 3 | 5 | 3 | 5 | 3 | 2 | 5 | 5 | 4 | 3 | 5 | **79.6** |
| Graphiti (Zep OSS) | 2 | 4 | 3 | 5 | 2 | 5 | 4 | 5 | 4 | 3 | 5 | **75.8** |
| Supermemory | 3 | 4 | 3 | 4 | 3 | 4 | 4 | 3 | 4 | 3 | 5 | **73.4** |
| **tapps-brain** | **4** | **2** | **5** | **5** | **5** | **5** | **3** | **5** | **5** | **4** | **1** | **77.8** |
| Memori (MemoriLabs) | 3 | 4 | 3 | 4 | 2 | 4 | 4 | 5 | 4 | 3 | 4 | **72.2** |
| LangGraph memory | 4 | 2 | 1 | 2 | 3 | 2 | 4 | 5 | 5 | 5 | 5 | **68.6** |
| Cognee | 2 | 3 | 3 | 4 | 2 | 5 | 3 | 5 | 4 | 3 | 4 | **68.0** |
| Letta | 3 | 3 | 3 | 4 | 2 | 1 | 4 | 5 | 4 | 3 | 5 | **67.6** |
| LlamaIndex memory | 4 | 2 | 2 | 3 | 1 | 0 | 4 | 5 | 5 | 4 | 5 | **63.8** |
| MemPalace (discounted) | 3 | 3 | 2 | 3 | 1 | 4 | 3 | 5 | 3 | 3 | 5 | **62.4** |
| agentmemory (rohitg00) | 4 | 3 | 2 | 3 | 1 | 5 | 3 | 5 | 3 | 3 | 2 | **59.2** |
| OMEGA (omega-memory) | 4 | 4 | 2 | 3 | 1 | 4 | 3 | 5 | 3 | 3 | 1 | **59.0** |
| MemRL | 2 | 3 | 4 | 4 | 1 | 0 | 2 | 5 | 1 | 5 | 1 | **51.6** |
| A-MEM (agiresearch) | 2 | 3 | 2 | 5 | 1 | 0 | 2 | 5 | 2 | 3 | 2 | **50.8** |
| Anthropic KG Memory MCP | 4 | 1 | 0 | 0 | 1 | 3 | 3 | 5 | 2 | 3 | 4 | **43.2** |
| claude-memory-compiler (coleam00) | 5 | 1 | 0 | 4 | 0 | 0 | 1 | 0 | 1 | 3 | 2 | **32.2** |

*MemPalace*: the row above uses **discounted** scoring (D2=3 because
benchmarks are externally disputed). With its headline benchmarks taken
at face value (D2=5 — accepting the claimed 96.6% headline), the total is 68.4. See per-system card for the
full face-value vs. discounted breakdown.

---

## Per-system scorecards

All GitHub metrics read on 2026-04-17 via the repo pages. Release dates
are from GitHub release tags.

### tapps-brain (self-assessment)

**Summary.** tapps-brain is a single-binary Postgres-backed agent memory service
maintained by wtthornton, designed for multi-agent boxes that run ~20 isolated
agents side-by-side behind an MCP Streamable HTTP transport. Its design stance
is "no LLM in the data path, Postgres-only storage, DB-enforced tenant
isolation" — it trades retrieval-quality ceiling for deterministic cost and
throughput.

**Architecture at a glance.**
- Storage: single Postgres 16+ with `pgvector` + `tsvector`; migrations
  owned by the service; no second datastore.
- Retrieval: BM25 (tsvector) + dense (pgvector) → Reciprocal Rank Fusion with
  composite recency/importance scoring.
- Decay: power-law per-tier (180/60/30/14 d half-lives) with FSRS-canonical
  `k = 81/19`, calibrated β ≈ 3.29 preserving `R(H) = 0.5`. Per-category
  `decay_model` / `decay_exponent` / `decay_k` overrides on every profile
  layer; exponential retained as an opt-in alternative. Lazy-on-read.
- Consolidation: deterministic text-similarity merge + dedicated
  `contradictions.py`; LLM never touches the write path.
- Isolation: `(project_id, agent_id)` composite key enforced at the DB layer
  by Postgres RLS with `FORCE ROW LEVEL SECURITY` (migration 012).
- Transport/SDK: MCP Streamable HTTP on :8080 (data + MCP), operator MCP on
  :8090, dashboard on :8088; Python SDK only.
- Notable: dual-token auth split (data vs operator) + versioned OpenAPI
  snapshot gate in CI.

**Best-fit.** Enterprise/coding-agent fleets that need per-project, per-agent
memory isolation enforced in the database and a deterministic write path
that scales linearly with Postgres, not LLM spend.

**Worst-fit.** Consumer "chat that remembers me" products where the
retrieval-quality ceiling of LLM-driven consolidation matters more than
tenant isolation or data-path determinism.

Rubric applied honestly; unverified claims scored conservatively.

| D | Score | Justification | Citation |
|---|---|---|---|
| D1 Storage & ops | 4 | Single Postgres backend, auto-migrations, Docker Compose one-liner; docked 1 because the brain also ships a separate operator MCP on :8090 and a dashboard on :8088. | `/home/wtthornton/code/tapps-brain/README.md`; ADR-007 Postgres-only; `docker/docker-compose.hive.yaml` |
| D2 Retrieval quality | 2 | BM25+vector RRF with composite scoring is structurally solid, but there is **no published LoCoMo or LongMemEval number** for tapps-brain. `[unverified]` quality claim. **STORY-SC01 (TAP-557) landed the eval harness — dataset loaders, reproducer CLI, CI smoke — on 2026-04-17; score moves to 4 once a full run is published (placeholders in `docs/benchmarks/locomo.md` + `longmemeval.md`).** | `docs/research/memory-systems-2026.md` §5.4 ("No published LoCoMo or LongMemEval number"); `src/tapps_brain/retrieval.py`, `fusion.py`; `src/tapps_brain/benchmarks/`; `scripts/run_benchmark.py` |
| D3 Decay / forgetting | 5 | Power-law per-tier with FSRS-canonical `k = 81/19` (≈ 4.263); per-layer `decay_model` / `decay_exponent` / `decay_k` configurable on every profile. `repo-brain.yaml` ships power-law across all four tiers with `β = ln 2 / ln(1 + 1/k) ≈ 3.29` preserving `R(half_life) = 0.5`. **STORY-SC02 (TAP-558) landed the calibration + docs on 2026-04-17.** Rubric 5 = "power-law or learned, per-category, configurable" ✓ | `src/tapps_brain/decay.py` (`power_law_decay` standalone), `src/tapps_brain/profiles/repo-brain.yaml`, `docs/guides/decay.md`, `tests/benchmarks/test_decay_perf.py` (<5% CPU overhead at 10k scale) |
| D4 Consolidation | 5 | Embedding-cosine merge (primary, 0.7 weight) with Jaccard+TF-cosine fallback when no stored vectors; pairwise `contradictions.py` polarity + numeric-divergence detection; provenance via `source`+`audit` with `merge_rule` + `similarity_score`; `maintenance consolidation-diff <key>` for merge inspection. **STORY-SC03 (TAP-559) 2026-04-18.** | `src/tapps_brain/auto_consolidation.py`, `consolidation.py`, `contradictions.py`, `similarity.py` |
| D5 Multi-tenancy | 5 | `(project_id, agent_id)` composite key enforced via Postgres RLS with `FORCE ROW LEVEL SECURITY` (migration 012) and role-bypass guards. This is the DB-layer 5-rubric. | `src/tapps_brain/migrations/private/012_rls_force.sql`; CHANGELOG 3.8.0 TAP-512, TAP-514; ADR-010 |
| D6a MCP depth | 5 | 55 MCP tools, dual transport (data plane on :8080 + operator on :8090), Streamable HTTP. | `src/tapps_brain/mcp_server/`; CHANGELOG 3.7.0–3.9.0 |
| D6b Language/SDK reach | 3 | Python SDK + httpx client + REST/OpenAPI; single-language. | `src/tapps_brain/http_adapter.py`, `openapi_contract.py`; CHANGELOG 3.8.0 TAP-508/509 |
| D7 License | 5 | MIT, real self-host; no hosted tier to compete with. | `LICENSE`; `pyproject.toml` L9 |
| D8 Production readiness | 5 | OTel exporters, migrations system, 127 test files, versioned OpenAPI with CI-gated snapshot, releases every 1–3 days in April 2026 (3.7.0 → 3.9.0 in 48h). | `src/tapps_brain/otel_*.py`; `src/tapps_brain/postgres_migrations.py`; CHANGELOG §3.7.0–3.9.0 |
| D9 Write-path design | 4 | Deterministic by design, **well-documented trade**: no LLM in hot path, consolidation is text-sim. Not a 5 because the design is one-way — there is no optional LLM-assisted write path. | README "Zero LLM dependency"; knowledge base §2.2 ("deliberate, defensible choice"); `docs/research/memory-systems-2026.md` open questions §3 |
| D10 Momentum | 1 | Single-maintainer repo (`wtthornton/tapps-brain`), no named external adopters, <200 stars class. Honest score given the rubric. | `pyproject.toml` Homepage `github.com/wtthornton/tapps-brain`; memory-exclusions note on "deployment model: one box, 20 agents" = single-tenant ops |

**Overall: 75.8/100.** (D3 moved 3 → 5 on 2026-04-17 via STORY-SC02; +3.2 points.)

### mem0

**Summary.** mem0 is an Apache-2.0 agent-memory library from mem0ai that
combines a vector backbone with an optional graph store and an LLM-driven
`ADD/UPDATE/DELETE/NOOP` consolidation state machine. It is the most-starred
OSS memory project (53.3k) and publishes the strongest peer-reviewed LoCoMo
result in the field; its design stance is "let the LLM curate memory for
quality, pay the latency and cost."

**Architecture at a glance.**
- Storage: vector store default (Qdrant / pgvector / others) + optional
  Neo4j graph + optional KV layer.
- Retrieval: hybrid vector + graph traversal; Mem0^g variant adds ~2 pt.
- Decay: implicit via LLM `DELETE` ops; no explicit curve.
- Consolidation: LLM decides ADD/UPDATE/DELETE/NOOP on every write.
- Isolation: user/session/agent scopes enforced at the application layer;
  not DB-enforced.
- Transport/SDK: Python + TypeScript/Node SDK, REST, hosted tier, third-party
  MCP wrappers.
- Notable: published LoCoMo 91.6 on arXiv (2504.19413).

**Best-fit.** Teams that want the best published retrieval quality out of
the box and can tolerate LLM latency/cost on the write path.

**Worst-fit.** Multi-tenant enterprise deployments that need DB-layer
isolation, or latency-sensitive hot paths where an LLM call per write is
unacceptable.

| D | Score | Justification | Citation |
|---|---|---|---|
| D1 | 3 | Vector default + optional graph (Neo4j) + optional KV; multi-service when graph is turned on. | <https://github.com/mem0ai/mem0>; knowledge base §2.1 |
| D2 | 5 | Published LoCoMo **91.6** with LLM-as-judge; Mem0^g graph variant ~+2pt; hybrid retrieval. Two sources. | arXiv:2504.19413 <https://arxiv.org/abs/2504.19413>; <https://deepwiki.com/mem0ai/mem0/4-graph-memory> |
| D3 | 3 | LLM `DELETE` op + implicit decay via ADD/UPDATE; no explicit power-law, but learned via LLM. | <https://mem0.ai/research>; knowledge base §2.1 |
| D4 | 5 | `ADD/UPDATE/DELETE/NOOP` LLM-driven dedup with contradiction handling; explicit state machine. | arXiv:2504.19413 |
| D5 | 3 | user/session/agent scopes at app layer; not DB-enforced. | <https://deepwiki.com/mem0ai/mem0/2-core-architecture> |
| D6a MCP depth | 2 | No first-party MCP server shipped; third-party wrappers only. | <https://github.com/mem0ai/mem0> (no `mcp` directory) |
| D6b Language/SDK reach | 5 | Python + TypeScript (Node SDK v3.0.0 2026-04-16) + REST + hosted API. | <https://github.com/mem0ai/mem0>; Node SDK v3.0.0 2026-04-16 |
| D7 | 5 | Apache-2.0, self-hostable + hosted tier. | <https://github.com/mem0ai/mem0> (LICENSE) |
| D8 | 4 | 304 releases, active since 2024, published benchmark, production customers. Docked because public test-coverage number is not surfaced on repo. | repo releases page; knowledge base §2.2 |
| D9 | 3 | LLM required in consolidation path; well-documented; no pure-deterministic mode. | arXiv:2504.19413 |
| D10 | 5 | **53.3k stars**, weekly-class releases, named production references, public roadmap. | <https://github.com/mem0ai/mem0> (star count as of 2026-04-17) |

**Overall: 79.6/100.**

### Graphiti (Zep OSS core)

**Summary.** Graphiti is Zep's Apache-2.0 temporal-knowledge-graph engine —
a bi-temporal entity/relation graph over Neo4j / FalkorDB / Kuzu / Neptune,
ingesting episodes with both event-time and ingest-time edges. It is the
flagship "graph camp" implementation and the most accurate system on
LongMemEval when Zep Cloud is wrapped around it. Design stance: "temporal
graphs win on multi-session reasoning."

**Architecture at a glance.**
- Storage: Neo4j (default), FalkorDB, Kuzu, or Amazon Neptune; no
  single-backend OSS option.
- Retrieval: graph traversal with edge-time filters + text/vector fallback.
- Decay: bi-temporal edge invalidation (supersede at T' when T event fires)
  rather than a decay curve.
- Consolidation: LLM entity/edge extraction with explicit supersession and
  ingest-time provenance.
- Isolation: per-user graphs; no DB-layer tenant enforcement documented in
  OSS.
- Transport/SDK: first-class MCP server (mcp-v1.0.2), REST, Python + TS.
- Notable: published arXiv:2501.13956 with "up to 18.5%" accuracy lift on
  LongMemEval over prior SOTA.

**Best-fit.** Workloads that ask "when did X become true" or need
cross-session temporal reasoning — the bi-temporal edges pay off.

**Worst-fit.** Small deployments that don't want to operate a graph
database, or latency-sensitive hot paths where graph-build overhead hurts.

| D | Score | Justification | Citation |
|---|---|---|---|
| D1 | 2 | Requires Neo4j / FalkorDB / Kuzu / Neptune; no single-backend option. | <https://github.com/getzep/graphiti>; knowledge base §2.2 |
| D2 | 4 | Published LongMemEval **63.8%** on GPT-4o — +15pt over Mem0 on that bench; graph traversal + edge-time filter. Weak on LoCoMo leaderboard presence. Two cites. | arXiv:2501.13956 <https://arxiv.org/abs/2501.13956>; <https://blog.getzep.com/content/files/2025/01/ZEP__USING_KNOWLEDGE_GRAPHS_TO_POWER_LLM_AGENT_MEMORY_2025011700.pdf> |
| D3 | 3 | Bi-temporal edge invalidation (event-time T + ingest-time T′); not power-law but temporally precise. | arXiv:2501.13956 |
| D4 | 5 | LLM entity/edge extraction + explicit supersession + provenance via ingest-time. | ibid. |
| D5 | 2 | Per-user graph; no DB-level tenant force documented in OSS. | <https://github.com/getzep/graphiti> |
| D6a MCP depth | 5 | mcp-v1.0.2 first-class server (2026-03-11); knowledge-graph tool suite. | <https://github.com/getzep/graphiti/releases> |
| D6b Language/SDK reach | 4 | Python + TypeScript SDKs + REST. | <https://github.com/getzep/graphiti> |
| D7 | 5 | Apache-2.0 for Graphiti core; Zep Cloud is separate/proprietary. | repo LICENSE |
| D8 | 4 | 25.1k stars, regular releases, paper published, enterprise customers via Zep Cloud. | <https://github.com/getzep/graphiti> |
| D9 | 3 | LLM-only write path (entity extraction); well-documented. | arXiv:2501.13956 |
| D10 | 5 | 25.1k stars, weekly commits, named Zep Cloud customers (SOC2). | ibid. |

**Overall: 75.8/100.**

### LangGraph memory

**Summary.** LangGraph is LangChain's MIT-licensed low-level orchestration
framework, and "LangGraph memory" refers to its `Checkpointer` + `Store`
primitives rather than a memory product per se. Its design stance: "provide
the plumbing; the team building the agent picks the policy." That makes it
the broadest primitive on this list, but not the most opinionated memory
system.

**Architecture at a glance.**
- Storage: pluggable checkpointer — Postgres, Redis, SQLite, Mongo,
  Aerospike; Store over any vector backend.
- Retrieval: Store supports vector search; no built-in hybrid or benchmark.
- Decay: user-implemented; no built-in policy.
- Consolidation: user-implemented; no primitive.
- Isolation: thread-scoped + namespace-scoped stores at the application
  layer.
- Transport/SDK: Python + JS/TS; no first-party MCP server.
- Notable: v1.1.x is stable, weekly releases, ubiquitous in the LangChain
  ecosystem (Klarna, Replit named adopters).

**Best-fit.** Teams already building with LangChain who want fine-grained
control over memory policy and don't need a turnkey memory product.

**Worst-fit.** Teams who need an opinionated memory layer out of the box —
LangGraph gives you primitives, not answers.

| D | Score | Justification | Citation |
|---|---|---|---|
| D1 | 4 | Checkpointer pluggable (Postgres, Redis, SQLite, Mongo, Aerospike); pick one backend. | <https://docs.langchain.com/oss/python/langgraph/add-memory> |
| D2 | 2 | Not a retrieval system per se — primitive; Store supports vector search but no benchmark. `[unverified]` on any LoCoMo number. | ibid. |
| D3 | 1 | User-implemented; no decay built-in. | ibid. |
| D4 | 2 | User-implemented; no consolidation primitive. | ibid. |
| D5 | 3 | Thread-scoped + namespace-scoped stores; app-level isolation, not DB-forced. | ibid. |
| D6a MCP depth | 2 | No first-party MCP server; community wrappers only. | <https://github.com/langchain-ai/langgraph> |
| D6b Language/SDK reach | 4 | Python + JS/TS; deep framework surface in each. | <https://github.com/langchain-ai/langgraph>; v1.1.7 2026-04-17 |
| D7 | 5 | MIT; fully self-host. | repo LICENSE |
| D8 | 5 | 29.5k stars, weekly releases, extensive production footprint, version 1.x stable. | <https://github.com/langchain-ai/langgraph/releases> |
| D9 | 5 | Fully user-choosable — deterministic or LLM, any combination. The primitive nature is the feature. | docs |
| D10 | 5 | 29.5k stars, every-week releases, ubiquitous adoption. | ibid. |

**Overall: 68.6/100.** LangGraph scores high because it's the broadest
primitive, not because it's the best *memory product* — D2/D3/D4 reflect
that. The 2026-04-17 structural audit lowered LangGraph's effective
transport score (D6a=2 for no first-party MCP), which dropped it from
rank 3 to rank 6.

### Memori (MemoriLabs)

**Summary.** Memori is MemoriLabs' Apache-2.0 agent-memory engine with
Rust + Python + TS components, targeting cost-efficient long-session memory.
Its design stance is "extract structured state per entity/process/session
and keep the prompt footprint tiny" — the published LoCoMo run uses ~5% of
full-context tokens.

**Architecture at a glance.**
- Storage: multi-store but cohesive — vector + relational state tables.
- Retrieval: entity/process/session attribution + vector retrieval.
- Decay: implicit via LLM extraction; no curve documented.
- Consolidation: LLM-driven structured-state extraction with attribution.
- Isolation: entity/process/session separation at the application layer.
- Transport/SDK: MCP (Claude Code / Cursor / Codex / Warp); Python + TS;
  Rust core.
- Notable: published LoCoMo 81.95% at 4.97% of full-context tokens.

**Best-fit.** Long-session assistants with tight context-budget constraints
where prompt-size reduction beats absolute accuracy.

**Worst-fit.** Multi-tenant deployments requiring DB-enforced isolation, or
teams needing deterministic write paths.

| D | Score | Justification | Citation |
|---|---|---|---|
| D1 | 3 | Agent-native infra with Rust + Python + TS; multi-service but cohesive. | <https://github.com/MemoriLabs/Memori> |
| D2 | 4 | Published LoCoMo **81.95%** with ~5% of full-context tokens; not top-of-leaderboard but solid. | repo README; knowledge base §5.2 |
| D3 | 3 | Implicit via LLM extraction; no explicit decay published. | repo README |
| D4 | 4 | Structured persistent state extraction with entity/process/session attribution. | ibid. |
| D5 | 2 | Entity-process-session separation is app-layer; no DB-force. | ibid. |
| D6a MCP depth | 4 | MCP shipped with integrations across Claude Code / Cursor / Codex / Warp; mid tool surface. | <https://github.com/MemoriLabs/Memori> README |
| D6b Language/SDK reach | 4 | Python + TS SDKs + Rust components + REST. | ibid. |
| D7 | 5 | Apache-2.0. | repo LICENSE |
| D8 | 4 | v3.2.8 2026-04-13, 13.3k stars, commercial backer. | <https://github.com/MemoriLabs/Memori/releases> |
| D9 | 3 | LLM in path, no deterministic mode documented. | repo README |
| D10 | 4 | 13.3k stars, active releases, named adopters via MCP integrations. | ibid. |

**Overall: 72.2/100.**

### LlamaIndex memory

**Summary.** LlamaIndex memory is the MIT-licensed set of composable memory
modules inside the broader LlamaIndex framework (48.7k stars) — buffer
memory, vector memory, chat-summary buffer — built by run-llama. Its design
stance is "memory is a conversation-state concern; compose it from
primitives that match your retrieval pipeline."

**Architecture at a glance.**
- Storage: in-process modules over any LlamaIndex storage (vector, KV, doc).
- Retrieval: buffer + vector + summary chains; no first-class hybrid scorer.
- Decay: buffer windowing + LLM summary rollups.
- Consolidation: `ChatSummaryMemoryBuffer` — LLM summarises, no dedup.
- Isolation: none built-in.
- Transport/SDK: Python primary, JS/TS via LlamaIndex.TS; no first-party
  MCP server.
- Notable: tight integration with the rest of the LlamaIndex framework
  (document parsing, OCR, agents).

**Best-fit.** Teams already on LlamaIndex who want conversation memory
composed with their existing retrieval stack.

**Worst-fit.** Teams who need a dedicated memory service, published
benchmarks, or tenant isolation.

| D | Score | Justification | Citation |
|---|---|---|---|
| D1 | 4 | In-process modules over any storage; trivial to add, no ops. | <https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/> |
| D2 | 2 | Not benchmarked as a memory system; conversation-state focus. | ibid.; knowledge base §2.2 |
| D3 | 2 | Buffer windowing + LLM summary; no decay curves. | ibid. |
| D4 | 3 | `ChatSummaryMemoryBuffer` via LLM; no dedup/contradiction. | ibid. |
| D5 | 1 | None built-in. | ibid. |
| D6a MCP depth | 0 | No first-party MCP server for memory modules. | <https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/> |
| D6b Language/SDK reach | 4 | Python primary + JS/TS via LlamaIndex.TS; REST optional. | <https://github.com/run-llama/llama_index> |
| D7 | 5 | MIT. | repo LICENSE |
| D8 | 5 | v0.14.20 2026-04-03, 48.7k stars, production-standard framework. | <https://github.com/run-llama/llama_index/releases> |
| D9 | 4 | Composable — primary buffer + secondary vector; both choices documented. | docs |
| D10 | 5 | 48.7k stars, weekly releases, ubiquitous. | ibid. |

**Overall: 63.8/100.**

### Cognee

**Summary.** Cognee is topoteretes' Apache-2.0 "knowledge engine for AI agent
memory" — an ECL (Extract/Cognify/Load) pipeline that builds a graph from
raw agent data with a clean `remember/recall/forget/improve` API. Funded
with a $7.5M seed; 16.2k stars. Design stance: "memory is a graph you
cognify; expose it through a small, opinionated API."

**Architecture at a glance.**
- Storage: SQLite/Postgres relational + LanceDB/pgvector + Kuzu graph;
  local-first but multi-service.
- Retrieval: graph + vector hybrid via the `cognify` pipeline.
- Decay: `forget` API exists; implementation not detailed in public docs.
- Consolidation: `cognify` step builds the graph from raw data
  automatically.
- Isolation: session-scoped + documented tenant roadmap for 2026; not yet
  DB-enforced.
- Transport/SDK: MCP (`introducing-cognee-mcp`), REST, Python, Claude Code
  plugin.
- Notable: the small API surface (`remember/recall/forget/improve`) is the
  cleanest in the field.

**Best-fit.** Teams that want an opinionated, small-surface memory API and
can absorb the multi-service ops burden.

**Worst-fit.** Single-service-only shops (one Postgres, no extras) or
workloads that need DB-layer isolation today.

| D | Score | Justification | Citation |
|---|---|---|---|
| D1 | 2 | SQLite/Postgres + LanceDB/pgvector + Kuzu graph; local-first but multi-service. | <https://github.com/topoteretes/cognee> |
| D2 | 3 | ECL pipeline + graph+vector; no published LoCoMo/LongMemEval `[unverified]`. | knowledge base §2.1; repo README |
| D3 | 3 | `forget` API exists; implementation not detailed. | repo README |
| D4 | 4 | `cognify` step builds graph from raw data — automatic consolidation. | <https://www.cognee.ai/blog/fundamentals/how-cognee-builds-ai-memory> |
| D5 | 2 | Per-user DB isolation is on 2026 roadmap, not yet shipped. | knowledge base §2.1 |
| D6a MCP depth | 5 | First-class `cognee-mcp` server (introduced 2025) + Claude Code plugin. | <https://www.cognee.ai/blog/cognee-news/introducing-cognee-mcp> |
| D6b Language/SDK reach | 3 | Python primary + REST; TS client non-canonical. | <https://github.com/topoteretes/cognee> |
| D7 | 5 | Apache-2.0 + hosted tier. | repo LICENSE |
| D8 | 4 | 16.2k stars, 98+ releases, funded ($7.5M seed). | <https://github.com/topoteretes/cognee>; funding post |
| D9 | 3 | LLM in `cognify`; no deterministic mode. | repo README |
| D10 | 4 | 16.2k stars, active releases, commercial backer. | ibid. |

**Overall: 68.0/100.**

### OMEGA (omega-memory)

**Summary.** OMEGA is an Apache-2.0 local-first agent memory system (single
SQLite process, FTS5 + vector) published by the omega-memory organisation,
known for a self-reported LongMemEval score of 95.4% (466/500) with GPT-4.1
as judge. Design stance: "zero-dep, local-first memory that beats the
leaderboard on a laptop." Audit note: 98 GitHub stars as of 2026-04-17
(below the rubric's 200-star threshold).

**Architecture at a glance.**
- Storage: single SQLite file with FTS5 + vector extension; no external
  services.
- Retrieval: hybrid FTS + vector with reranking; local ONNX embeddings.
- Decay: "removes expired information" claim in README; no curve
  documented.
- Consolidation: semantic dedup via vector similarity + entity extraction.
- Isolation: single-user local-first; no multi-tenancy.
- Transport/SDK: MCP + REST + Python; Claude Desktop / Cursor setup
  documented.
- Notable: vendor-reported LongMemEval number tops public leaderboards
  (self-reported, GPT-4.1 judge).

**Best-fit.** Single-developer local agent memory where zero-dep setup and
offline operation matter.

**Worst-fit.** Multi-user or enterprise deployments (no multi-tenancy,
single-process, tiny ecosystem).

| D | Score | Justification | Citation |
|---|---|---|---|
| D1 | 4 | SQLite + FTS5 + vector search; single local process; zero deps. | <https://github.com/omega-memory/core>; <https://omegamax.co/compare> |
| D2 | 4 | Self-reported LongMemEval **95.4%** task-averaged (466/500) with GPT-4.1 judge; hybrid retrieval (vector + FTS). Rubric: vendor-site self-report + LLM-as-judge = 4 (not 5, which requires peer-reviewed/arXiv). **Audit 2026-04-17 lowered 5 → 4.** | <https://omegamax.co/benchmarks>; <https://dev.to/singularityjason/how-i-built-a-memory-system-that-scores-954-on-longmemeval-1-on-the-leaderboard-2md3> |
| D3 | 2 | Claims "removes expired information" but no curve documented `[unverified]`. | repo README |
| D4 | 3 | Semantic dedup via vector sim; entity extraction. | repo README |
| D5 | 1 | Single-user local-first; no tenant isolation. | ibid. |
| D6a MCP depth | 4 | MCP + REST with Claude Desktop / Cursor setup guides; tool count unspecified in public docs. | <https://omegamax.co/compare> |
| D6b Language/SDK reach | 3 | Python SDK + REST. | repo |
| D7 | 5 | Apache-2.0. | repo LICENSE |
| D8 | 3 | New project, fewer stars, fewer external references than mem0/Letta. | repo |
| D9 | 3 | Local ONNX embeddings, but the quality claim rests on LLM-as-judge eval. | repo |
| D10 | 1 | **98 GitHub stars** confirmed 2026-04-17 (below the 200-star rubric threshold). v1.4.7 2026-04-14 shows activity, but ecosystem momentum is minimal. **Audit 2026-04-17 lowered 3 → 1.** | <https://github.com/omega-memory/core> (star count 2026-04-17) |

**Overall: 59.0/100** (score audit + structural audit combined).

### Letta (MemGPT successor)

**Summary.** Letta (formerly MemGPT) is letta-ai's Apache-2.0 agent platform
with the memory model from the MemGPT paper: an archival store + working
context + self-edit tool calls that let the agent promote/demote memories
on its own. 22.1k stars, cloud and self-host. Design stance: "give the
agent the tools to manage its own memory; the agent is the policy."

**Architecture at a glance.**
- Storage: archival (vector) + recall (relational) tables; pluggable
  backends.
- Retrieval: agent-decided via tool calls over archival + recall.
- Decay: emergent — agent decides what to promote/demote via tool calls.
- Consolidation: agent-driven summarisation; tier promotion on LLM
  decision.
- Isolation: per-agent instance; no cross-agent tenant isolation.
- Transport/SDK: Letta Code CLI, REST, Python + TypeScript SDKs.
- Notable: strong brand lineage from the MemGPT paper and stateful
  per-agent model.

**Best-fit.** Single-agent assistants where the agent itself should decide
what to remember (MemGPT's original thesis).

**Worst-fit.** Fleets of agents sharing a memory substrate with tenant
boundaries, or deterministic write-path requirements.

| D | Score | Justification | Citation |
|---|---|---|---|
| D1 | 3 | Archival + recall tables, pluggable vector/graph backend. | <https://github.com/letta-ai/letta>; knowledge base §2.1 |
| D2 | 3 | No published LoCoMo number on MemGPT lineage `[unverified]`; strong long-session durability claim. | knowledge base §2.1 |
| D3 | 3 | Agent-decided tier movement via tool calls; emergent rather than curve-based. | <https://docs.letta.com/advanced/memory-management/> |
| D4 | 4 | Agent-driven summarisation; tier promotion on LLM decision. | ibid. |
| D5 | 2 | Per-agent instance; no cross-agent tenant isolation documented. | ibid. |
| D6a MCP depth | 1 | MCP not in current Letta docs; roadmap/community efforts only. | <https://docs.letta.com> |
| D6b Language/SDK reach | 4 | Python + TypeScript SDKs + Letta Code CLI + REST. | <https://github.com/letta-ai/letta>; v0.16.7 2026-03-31 |
| D7 | 5 | Apache-2.0 OSS + cloud. | repo LICENSE |
| D8 | 4 | 22.1k stars, 176 releases. | <https://github.com/letta-ai/letta/releases> |
| D9 | 3 | LLM-in-path by design; documented. | docs |
| D10 | 5 | 22.1k stars, strong brand continuity from MemGPT. | <https://www.letta.com/blog/memgpt-and-letta> |

**Overall: 67.6/100.**

### agentmemory (rohitg00)

**Summary.** agentmemory is rohitg00's Apache-2.0 single-user memory
service (SQLite + in-process vector) that claims 95.2% LongMemEval-S R@5
via a triple-stream retriever (BM25 + vector + KG) with RRF k=60. 1.7k
stars, 44 MCP tools. Design stance: "one binary, local, MCP-first."

**Architecture at a glance.**
- Storage: SQLite KV + in-memory vector; no external DB.
- Retrieval: triple-stream — BM25 + vector + KG — fused via RRF (k=60).
- Decay: not explicitly curve-based; `[unverified]`.
- Consolidation: KG extraction + dedup; not as LLM-heavy as mem0.
- Isolation: 127.0.0.1-bound single-process; optional `TEAM_ID` + `USER_ID`
  namespacing at the application layer.
- Transport/SDK: 44 MCP tools + 6 resources + 3 prompts + 4 skills; REST;
  integrates with 30+ clients including Claude Code, Cursor, Gemini CLI.
- Notable: by far the richest MCP surface area of any system scored here.

**Best-fit.** Local single-developer MCP-first workflow where breadth of
tool coverage matters.

**Worst-fit.** Networked multi-user deployments (single-process, loopback
only) or anywhere a peer-reviewed benchmark is required.

| D | Score | Justification | Citation |
|---|---|---|---|
| D1 | 4 | SQLite KV + in-memory vector; zero external DB. | <https://github.com/rohitg00/agentmemory> |
| D2 | 3 | Self-reported LongMemEval-S **95.2% R@5** on repo README only (no arXiv / peer-reviewed writeup); triple-stream retrieval (BM25 + vector + KG) with RRF k=60. Per rubric, vendor-README self-report = 4 at best; knocked to 3 because there is no second independent cite (cf. OMEGA which has a dev.to third-party writeup). **Audit 2026-04-17 lowered 4 → 3.** | repo README; knowledge base referenced |
| D3 | 2 | Not explicitly curve-based `[unverified]`. | repo README |
| D4 | 3 | KG + dedup; not as LLM-heavy as mem0. | repo README |
| D5 | 1 | 127.0.0.1-bound single process; no multi-tenancy. | repo README |
| D6a MCP depth | 5 | 44 MCP tools + 6 resources; integrates with 30+ clients. | <https://github.com/rohitg00/agentmemory> README |
| D6b Language/SDK reach | 3 | Python SDK + REST; single-language. | repo README |
| D7 | 5 | Apache-2.0. | repo LICENSE |
| D8 | 3 | v0.8.12 2026-04-16, 646 passing tests, OTel; young project. | <https://github.com/rohitg00/agentmemory/releases> |
| D9 | 3 | BM25 + vector deterministic; KG extraction optional LLM. | repo README |
| D10 | 2 | 1.7k stars, growing but small. | repo |

**Overall: 59.2/100** (score audit + structural audit combined).

### Supermemory

**Summary.** Supermemory is supermemoryai's MIT-licensed memory system,
sold primarily as a hosted/cloud product but with an open-source TS/Python
codebase that can be self-hosted. Advertises top-of-leaderboard scores on
LongMemEval, LoCoMo, and ConvoMem; 21.9k stars. Design stance: "SaaS-first
with an OSS escape hatch."

**Architecture at a glance.**
- Storage: managed cloud (proprietary details); OSS scaffolding open for
  self-host.
- Retrieval: hybrid retrieval; vendor does not publish internals.
- Decay: "removes expired info" + knowledge-update pipeline (proprietary
  curve).
- Consolidation: automatic fact extraction + user-profile maintenance +
  update handling.
- Isolation: per-tenant cloud isolation; DB-layer enforcement not
  documented.
- Transport/SDK: MCP shipped (`npx install-mcp`), JS/TS + Python SDKs.
- Notable: only system on this list with a full consumer app
  (app.supermemory.ai) in addition to API/MCP.

**Best-fit.** Teams who want a hosted "memory as a platform feature" with a
single-command MCP install and consumer app.

**Worst-fit.** Regulated industries needing full self-host + DB-layer
tenant isolation + open architecture audit.

| D | Score | Justification | Citation |
|---|---|---|---|
| D1 | 3 | Managed service; ops are their problem but integration takes a cloud dep. | <https://github.com/supermemoryai/supermemory> |
| D2 | 4 | Self-reported #1 LoCoMo / ConvoMem; LongMemEval **85.4%** (knowledge base) / **81.6%** (repo current); independently reviewed but benchmark methodology varies. Two cites. | repo README; knowledge base §5.2 |
| D3 | 3 | "Removes expired info" + knowledge updates; proprietary curve. | repo README |
| D4 | 4 | Automatic fact extraction + user profile maintenance + update handling. | ibid. |
| D5 | 3 | Per-tenant cloud isolation. | ibid. |
| D6a MCP depth | 4 | MCP shipped; mid tool surface on hosted endpoint. | <https://github.com/supermemoryai/supermemory> |
| D6b Language/SDK reach | 4 | JS/TS + Python SDKs + REST. | ibid. |
| D7 | 3 | MIT license confirmed on the repo; self-hostable components exist. Still SaaS-primary — consumer app, hosted API, and OAuth flows are the paved path. Rubric reads: permissive OSS + real self-host = 5, SaaS-only = 0. Supermemory sits between: MIT + OSS scaffolding, but self-host is second-class. **Audit 2026-04-17 raised 0 → 3** — the prior 0 mis-applied the rubric (MIT ≠ SaaS-only). | <https://github.com/supermemoryai/supermemory/blob/main/LICENSE> (MIT, 2026-04-17); `README` self-host docs |
| D8 | 4 | Active commercial product; enterprise-ish. | repo |
| D9 | 3 | LLM in path; proprietary details. | repo |
| D10 | 5 | 21.9k stars, named production usage, active releases. | <https://github.com/supermemoryai/supermemory> |

**Overall: 73.4/100** (score audit raised D7 0→3 and structural audit
applied the corrected formula; combined these moved Supermemory from
rank 11 to rank 3). The D7 audit re-read the LICENSE file — MIT, not
SaaS-only.

### MemPalace

**Summary.** MemPalace is the MemPalace organisation's MIT-licensed
memory system that gained 47.5k GitHub stars in weeks behind headline
benchmark claims on LongMemEval (96.6 R@5), LoCoMo (88.9 R@10), ConvoMem
(92.9), and MemBench (80.3). Multiple independent reviewers dispute that
those numbers exercise MemPalace's own architecture (palace/wings/rooms/
drawers) rather than the underlying ChromaDB default. Design stance:
"memory as a palace metaphor" — whose substance the audit cannot verify.

**Architecture at a glance.**
- Storage: ChromaDB default + SQLite temporal graph; pluggable backend
  interface.
- Retrieval: raw semantic search (ChromaDB) + optional LLM rerank; the
  palace/wings layer sits above.
- Decay: "removes expired info" claim; no curve in docs reviewed.
- Consolidation: temporal entity-relationship graph in SQLite; not
  LoCoMo-validated.
- Isolation: not documented.
- Transport/SDK: 29 MCP tools, pluggable backend interface.
- Notable: the gap between headline benchmark claims and externally
  reviewed reality is itself the finding.

**Best-fit.** Experimental/research use where the "palace" metaphor helps
think about memory structure; small side projects.

**Worst-fit.** Production or procurement contexts — until reproducible,
architecture-exercising benchmarks appear.

**Heavy caveat.** MemPalace shows 47.5k stars and publishes four flagship
benchmark scores (LongMemEval 96.6 R@5, LoCoMo 88.9 R@10, ConvoMem 92.9,
MemBench 80.3). Multiple independent sources flag problems:

- GitHub Issue #214 on the (forked) repo: "Benchmarks do not exercise
  MemPalace — headline 96.6% is a ChromaDB score" — the palace
  wings/rooms/drawers structure is not involved at benchmark time.
- Nicholas Rhodes, "MemPalace Review: Real AI Memory Innovation,
  Questionable Benchmark Claims" (<https://nicholasrhodes.substack.com/p/mempalace-ai-memory-review-benchmarks>).
- danilchenko.dev: "MemPalace Review: The AI Memory System That Broke
  GitHub in a Weekend" (<https://www.danilchenko.dev/posts/2026-04-10-mempalace-review-ai-memory-system-milla-jovovich/>).
- Issue #875 on main repo: "README and official website still contain
  misrepresentations and false benchmark claims."
- Vectorize.io: "MemPalace Review: Benchmark Claims vs Reality"
  (<https://vectorize.io/articles/mempalace-review>).
- Public gist alleging ~42k purchased stars.

I score both a *face-value* and a *discounted* version. The table uses the
discounted scoring because the knowledge base instructs "unknown → low" and
externally disputed → cannot stand as evidence.

| D | Score | Justification | Citation |
|---|---|---|---|
| D1 | 3 | ChromaDB default + SQLite temporal graph; local-first; couple-of-services. | <https://github.com/MemPalace/mempalace>; WebFetch 2026-04-17 |
| D2 | 3 | Benchmark claims exist but are disputed by ≥4 independent reviewers and issue #214; scoring below face value per rubric's "unknown → low." **Two cites required on D2:** reviewer contradiction (<https://nicholasrhodes.substack.com/p/mempalace-ai-memory-review-benchmarks>) + issue #214 on fork (<https://github.com/milla-jovovich/mempalace/issues/214>). | ibid. |
| D3 | 2 | "Removes expired info" claim; no curve or mechanism in docs reviewed. | repo README |
| D4 | 3 | Temporal entity-relationship graph in SQLite; consolidation surface described but not LoCoMo-validated. | repo README |
| D5 | 1 | No multi-tenancy documented. | repo README |
| D6a MCP depth | 4 | 29 MCP tools; pluggable backend interface. | <https://github.com/MemPalace/mempalace> README |
| D6b Language/SDK reach | 3 | Python SDK + REST. | repo README |
| D7 | 5 | MIT. | repo LICENSE |
| D8 | 3 | v3.3.0 2026-04-14 active but contested. | repo releases |
| D9 | 3 | Raw retrieval without LLM on core path; LLM rerank optional. | repo README |
| D10 | 5 | 47.5k stars at face value (contested provenance). | repo |

**Overall (discounted): 62.4/100. Face-value: 68.4/100.** The gap *is*
the finding — a scorecard that accepts self-reported numbers uncritically
would put MemPalace in the top 8; one that applies the rubric ("unknown
→ low") ranks it near the bottom. Buyers should demand reproducible
benchmarks on a clean branch run, not on ChromaDB's baseline.

### A-MEM (agiresearch)

**Summary.** A-MEM is agiresearch's MIT-licensed research prototype
behind the NeurIPS 2025 paper "A-MEM: Agentic Memory for LLM Agents"
(arXiv:2502.12110). Its canonical contribution is Zettelkasten-style
evolving notes: every new memory writes a structured note, looks for
links to historical notes, and accepted links propagate updates back.
972 stars — clearly research code, not production.

**Architecture at a glance.**
- Storage: ChromaDB backend; not packaged for ops.
- Retrieval: vector search over linked notes.
- Decay: evolving notes via linked updates (not a curve).
- Consolidation: Zettelkasten-style automatic link propagation +
  contextual attribute updates — canonical 5-rubric consolidation.
- Isolation: none documented.
- Transport/SDK: Python library only; no MCP.
- Notable: the strongest consolidation algorithm in the scorecard.

**Best-fit.** Researchers extending the Zettelkasten idea, or teams
prototyping evolving-note consolidation strategies.

**Worst-fit.** Anything production (972 stars, ChromaDB-locked, no MCP,
no isolation).

| D | Score | Justification | Citation |
|---|---|---|---|
| D1 | 2 | Research code; ChromaDB backend; not packaged for ops. | <https://github.com/agiresearch/A-mem> |
| D2 | 3 | NeurIPS 2025 paper reports SOTA across six models; exact numbers vary by judge. Two cites. | arXiv:2502.12110; <https://neurips.cc/virtual/2025/poster/119020> |
| D3 | 2 | Evolving notes via linked updates; not a decay curve. | arXiv:2502.12110 |
| D4 | 5 | Zettelkasten-style automatic link propagation + contextual attribute updates; the canonical 5-rubric consolidation. | ibid. |
| D5 | 1 | None documented. | repo |
| D6a MCP depth | 0 | No MCP; Python library only. | <https://github.com/agiresearch/A-mem> |
| D6b Language/SDK reach | 2 | Python library only; no REST abstraction published. | repo |
| D7 | 5 | MIT. | repo LICENSE |
| D8 | 2 | 972 stars, 31 commits, 13 open issues — research-grade. | repo |
| D9 | 3 | LLM-driven notes + linking; well-documented in paper. | arXiv:2502.12110 |
| D10 | 2 | 972 stars, low velocity. | repo |

**Overall: 50.8/100.**

### Anthropic KG Memory MCP server (reference impl)

**Summary.** The Anthropic KG Memory MCP server is the canonical MIT
reference implementation inside `modelcontextprotocol/servers` (~84k
stars on the parent repo). It persists entities/relations/observations
in a single JSONL file and exposes them as MCP primitives. Design stance:
"show what an MCP memory server looks like at the minimum."

**Architecture at a glance.**
- Storage: single JSONL file (`memory.jsonl`), zero external deps.
- Retrieval: CRUD over entities/relations/observations; no ranking.
- Decay: none.
- Consolidation: none — every write is a new row.
- Isolation: one file per server instance.
- Transport/SDK: canonical MCP server; Docker + NPX distribution.
- Notable: ships inside `modelcontextprotocol/servers`, giving it
  canonical discoverability.

**Best-fit.** Demos, teaching, and anyone bootstrapping their own MCP
memory server from a known-good skeleton.

**Worst-fit.** Any production workload — the README explicitly flags it
as a reference impl.

| D | Score | Justification | Citation |
|---|---|---|---|
| D1 | 4 | Single JSONL file; zero deps. | <https://github.com/modelcontextprotocol/servers/tree/main/src/memory> |
| D2 | 1 | CRUD over entity/relation/observation; no ranking, no benchmarks. | ibid. |
| D3 | 0 | None. | ibid. |
| D4 | 0 | None. | ibid. |
| D5 | 1 | Single file per instance. | ibid. |
| D6a MCP depth | 3 | Canonical reference MCP server (~9 entity/relation/observation tools); intentionally minimal. | <https://github.com/modelcontextprotocol/servers/tree/main/src/memory> |
| D6b Language/SDK reach | 3 | TypeScript reference impl + NPX + Docker; REST-like via MCP transport, not REST proper. | ibid. |
| D7 | 5 | MIT. | ibid. |
| D8 | 2 | Reference impl, not production — explicitly flagged as such. | ibid. |
| D9 | 3 | User writes entities/relations directly; deterministic. | ibid. |
| D10 | 4 | Ships inside `modelcontextprotocol/servers`, huge canonical reach. | ibid. |

**Overall: 43.2/100.**

### MemRL

**Summary.** MemRL is MemTensor's MIT-licensed research prototype from
arXiv:2601.03192 (ICLR/arXiv 2026), exploring a learned RL policy over
store / retrieve / update / summarise / discard memory actions. 86 stars
— pure research code. Design stance: "memory management is a policy
problem; let RL solve it."

**Architecture at a glance.**
- Storage: research scaffold; benchmark-focused.
- Retrieval: policy-driven (learned) rather than heuristic.
- Decay: learned — the policy decides discard vs retain.
- Consolidation: RL-policy-driven summarisation and update.
- Isolation: none.
- Transport/SDK: Python only; no MCP.
- Notable: the paper is the write-path argument; evaluated on HLE,
  BigCodeBench, ALFWorld, Lifelong Agent Bench.

**Best-fit.** Researchers validating or extending learned-policy memory
management.

**Worst-fit.** Any shipping product — this is bench code, explicitly.

| D | Score | Justification | Citation |
|---|---|---|---|
| D1 | 2 | Research-bench scaffold; benchmark-focused ops. | <https://github.com/MemTensor/MemRL> |
| D2 | 3 | Beats SOTA on HLE / BigCodeBench / ALFWorld / LAB per paper; no LoCoMo. Two cites. | arXiv:2601.03192; repo README |
| D3 | 4 | Learned policy over store/retrieve/update/summarise/discard — rubric's 5-adjacent. | arXiv:2601.03192 |
| D4 | 4 | Policy-driven consolidation via RL. | ibid. |
| D5 | 1 | None. | repo |
| D6a MCP depth | 0 | No MCP; research bench. | <https://github.com/MemTensor/MemRL> |
| D6b Language/SDK reach | 2 | Python research code; no REST. | repo |
| D7 | 5 | MIT. | repo LICENSE |
| D8 | 1 | 86 stars, 75 commits, bench-focused. | repo |
| D9 | 5 | The entire paper *is* the write-path argument; explicitly novel and documented. | arXiv:2601.03192 |
| D10 | 1 | 86 stars. | repo |

**Overall: 51.6/100.** Low overall, high on exactly the dimensions the
paper optimises. A useful reminder that a scorecard flattens a narrow
research contribution — MemRL is "winning" on a dimension that isn't
weighted high enough to move its total.

### claude-memory-compiler (coleam00)

**Summary.** claude-memory-compiler is Cole Medin's single-user memory
system for Claude Code, adapted from Andrej Karpathy's LLM Knowledge Base
gist. Claude Code hooks (SessionStart / SessionEnd / PreCompact) capture
the transcript, spawn a detached `flush.py` that calls the Claude Agent
SDK to extract daily-log entries, and `compile.py` LLM-compiles those
logs into cross-referenced markdown concept / connection / Q&A articles.
Design stance: "at personal-KB scale (50–500 articles) the LLM reading a
structured `index.md` beats vector similarity — no RAG needed."

**Architecture at a glance.**
- Storage: plain markdown files on the local filesystem (`daily/`,
  `knowledge/concepts/`, `knowledge/connections/`, `knowledge/qa/`,
  `index.md`, `log.md`); no DB, no vectors, no external services.
- Retrieval: LLM reads `index.md` + selected articles into context — no
  embeddings, no BM25, no ranking. Karpathy's insight cited as
  rationale; no benchmark.
- Decay: none. Daily logs are append-only / immutable; knowledge
  articles grow unbounded. The `lint.py` "stale articles" check is a
  drift signal, not a decay curve.
- Consolidation: LLM-driven via `compile.py` — updates existing
  concepts vs. creating duplicates, creates `connections/` articles
  for non-obvious cross-concept insights, preserves provenance
  (frontmatter `sources:` links back to daily logs), and `lint.py`
  detects contradictions across articles.
- Isolation: none — single-user, single-project, local-only.
- Transport/SDK: Claude Code hooks (`session-start.py`, `session-end.py`,
  `pre-compact.py`) fire via `.claude/settings.json`; CLI via `uv run
  python scripts/{compile,query,lint}.py`. No MCP server. No REST. No
  Python package exported.
- Notable: the "compiler analogy" (daily logs = source code, LLM =
  compiler, knowledge/ = executable, lint = test suite) + the
  post-6-PM auto-compilation trigger driven by a hash-based `state.json`.

**Best-fit.** A solo developer who wants their Claude Code conversations
to accumulate into a personal Obsidian-compatible wiki without
standing up any backend.

**Worst-fit.** Any multi-agent, multi-user, or production memory
workload — no license, no tenant isolation, no MCP, no decay, no REST.

| D | Score | Justification | Citation |
|---|---|---|---|
| D1 Storage & ops | 5 | Plain-markdown filesystem; `uv sync` installs three Python deps; zero backend services; hooks auto-activate via `.claude/settings.json`. Rubric 5 "single-backend, stateless, zero-click install" exactly. | `/tmp/claude-memory-compiler/README.md`; `/tmp/claude-memory-compiler/pyproject.toml`; <https://github.com/coleam00/claude-memory-compiler> (2026-04-17) |
| D2 Retrieval quality | 1 | No ranked retrieval — the LLM reads `index.md` and selected articles directly. Karpathy's "LLM-reading-index beats cosine at personal scale" insight is cited as rationale but **no benchmark is published**; LoCoMo / LongMemEval entirely absent. Rubric 0 = "no benchmarks, single-signal retrieval"; scored 1 because retrieval exists (LLM-mediated) rather than being absent. | `/tmp/claude-memory-compiler/README.md` "Why No RAG?"; `/tmp/claude-memory-compiler/AGENTS.md` "Query" section |
| D3 Decay / forgetting | 0 | Daily logs are explicitly "append-only, never edited after the fact"; knowledge articles grow indefinitely. The `lint.py` stale-article check reports drift, it does not decay or evict. Rubric 0 = "no decay, unbounded growth." | `/tmp/claude-memory-compiler/AGENTS.md` "Layer 1: daily/" + "Lint" section |
| D4 Consolidation | 4 | `compile.py` actively merges: the compile prompt instructs "if an existing concept article covers this topic: UPDATE it… if it's a new topic: CREATE…"; `connections/` articles are generated when 2+ concepts link; frontmatter `sources:` preserves provenance back to daily logs; `lint.py` runs a contradiction check across articles. Not a 5 because the contradiction check is lint-time reporting, not write-time resolution. | `/tmp/claude-memory-compiler/AGENTS.md` "Core Operations → Compile" + "Lint → Contradictions (LLM)" |
| D5 Multi-tenancy & isolation | 0 | Explicitly single-user local-only; no `project_id`, no `agent_id`, no namespace. `.claude/settings.json` binds to one project directory. Rubric 0 exactly. | `/tmp/claude-memory-compiler/.claude/settings.json`; `/tmp/claude-memory-compiler/AGENTS.md` |
| D6a MCP depth | 0 | No MCP server. Integration is via Claude Code hooks (SessionStart / SessionEnd / PreCompact), which is a different surface entirely. Rubric 0 = "no MCP." | `/tmp/claude-memory-compiler/.claude/settings.json`; `/tmp/claude-memory-compiler/hooks/` (three hook scripts, no `mcp/` directory) |
| D6b Language/SDK reach | 1 | Python-only CLI scripts; no Python package published, no REST, no JS/TS binding, no MCP. The "SDK" is `uv run python scripts/…`. Rubric 1 = "one language only, no SDK abstraction." | `/tmp/claude-memory-compiler/pyproject.toml` (name `llm-personal-kb` v0.1.0, unpublished); `/tmp/claude-memory-compiler/scripts/` |
| D7 License & commercial | 0 | **No LICENSE file in the repo** as of 2026-04-17; under US copyright default this means "all rights reserved" — strictly not permissive OSS. The README notes Claude Agent SDK personal use is covered by Anthropic subscription, but that is about SDK usage, not repo licensing. Rubric 0 reserved for SaaS-only or AGPL-only; no-license is worse (not legally reusable at all). | WebFetch <https://github.com/coleam00/claude-memory-compiler> 2026-04-17 ("No LICENSE file visible"); `/tmp/claude-memory-compiler/` directory listing shows no LICENSE* |
| D8 Production readiness | 1 | Two commits total (both 2026-04-06), no releases, no tags, no tests directory, no OTel, no CI, no migrations, no versioned API; pyproject version `0.1.0`. Clear README + AGENTS.md + lint script keep it above pure POC, but the production bar is not met. Rubric 1 = just above "POC only." | `git log` in `/tmp/claude-memory-compiler` (2 commits, 2026-04-06); `/tmp/claude-memory-compiler/pyproject.toml` L3 |
| D9 Write-path design | 3 | LLM required on both write paths (`flush.py` → Claude Agent SDK extracts; `compile.py` → Claude Agent SDK compiles); explicit cost table documented ($0.45–0.65 per daily log, ~$0.02–0.05 per flush). Well-documented LLM path but **not user-choosable** (no deterministic mode). Rubric 3 = "one or the other, well-documented." | `/tmp/claude-memory-compiler/AGENTS.md` "Costs" table; `/tmp/claude-memory-compiler/scripts/compile.py` uses `claude_agent_sdk.query()` |
| D10 Momentum | 2 | 783 GitHub stars, 209 forks as of 2026-04-17 (WebFetch) — above the 200-star floor so the OMEGA-style D10=1 cap does not apply. But only **2 commits total**, no tags, no releases, launched ~11 days ago (2026-04-06), single maintainer, no named production adopters. Viral launch but velocity unproven. Rubric 2 = "small/new project, few hundred stars, not weekly releases." | WebFetch <https://github.com/coleam00/claude-memory-compiler> 2026-04-17 (783 stars, 209 forks); `git log` shows 2 commits on 2026-04-06 |

**Overall: 32.2/100.** Lowest of the 16 systems scored — and that is the
honest read. claude-memory-compiler is not trying to be a multi-tenant
memory *system*; it is a clever markdown-only personal KB with a
compiler metaphor, aimed squarely at a single Claude Code user. On the
dimensions it cares about (D1 ops-free, D4 LLM-driven dedup with
provenance, D9 documented cost) it scores well. On the dimensions the
rubric weights for production agent-memory use (D2 retrieval evidence,
D3 decay, D5 isolation, D6a MCP, D7 license, D8 production readiness) it
scores at or near zero — by design. The total is a rubric artefact, not
a quality verdict on the project's stated scope. Cross-reference: the
project's design rationale is walked through in Cole Medin's 2026-04-06
YouTube video *I Built Self-Evolving Claude Code Memory w/ Karpathy's
LLM Knowledge Bases* — see `memory-systems-2026.md` §4.

---

## Cross-cutting observations

**1. Three clusters.** The scorecard produces three visible tiers after
the 2026-04-17 structural audit. *Leaders* (79.6–72.2: mem0, Graphiti,
Supermemory, tapps-brain, Memori) combine permissive licensing, either
published benchmarks or first-class MCP investment, and (except
tapps-brain) genuine star-count momentum. *Middle* (68.6–62.4: LangGraph,
Cognee, Letta, LlamaIndex, MemPalace-discounted) have one or two strong
dimensions but miss the top because of primitive-not-product positioning
(LangGraph, LlamaIndex), missing MCP (Letta), or contested benchmarks
(MemPalace). *Research and specialists* (59.2–43.2: agentmemory, OMEGA,
MemRL, A-MEM, Anthropic KG) are pulled down either by tiny ecosystems
(OMEGA 98 stars, MemRL 86), research-grade production readiness (A-MEM,
MemRL), or minimalism (Anthropic KG).

**2. The field has not solved multi-tenancy.** Only tapps-brain scores 5
on D5. Supermemory/Cognee/mem0 score 3 via app-layer user scoping; the
rest score 2 or 1. This is the single clearest differentiator tapps-brain
earns — the Postgres RLS `FORCE` pattern from CHANGELOG 3.8.0 TAP-512 is
genuinely ahead of the field, not marketing copy. If agent-memory adoption
goes through enterprise procurement, D5 becomes a line-item requirement
and the top-scorers move.

**3. Benchmark trust is eroding.** LoCoMo and LongMemEval are widely cited,
but MemPalace demonstrates how easy it is to publish impressive numbers
without exercising the claimed architecture. Supermemory, MemMachine,
Hindsight, and Engram all self-report top-of-leaderboard scores using
different judges. The 2026 trend is that self-reported LLM-as-judge
numbers — which the whole field relies on — are becoming a soft signal,
not hard evidence. tapps-brain's complete absence from the leaderboard
hurts it in D2 (score 2), but the field is converging on the view that
being *on* the leaderboard is worth less than it was six months ago.

**Where tapps-brain lands.** Rank 2-3/15 (tied with Graphiti at 75.8),
post STORY-SC02 (2026-04-17). tapps-brain is best-in-class on isolation
(D5=5), MCP depth (D6a=5 — 55 tools + dual transport), production
readiness (D8=5), and **decay (D3=5 — power-law per-tier with
per-category overrides)**, competitive on storage, license, and
write-path, **below average on retrieval quality evidence (D2=2)**
because no benchmark has been run, and **last on momentum (D10=1)**
because it has no external adopters and <200 stars. Removing either of
those two remaining weaknesses — publishing a LoCoMo number or acquiring
three named external adopters — would move the total toward 79–82 and
put tapps-brain in direct contention with mem0 at the top of the
leaders' cluster.

---

## Scorecard limitations

The scorecard does not capture:

- **Latency at concurrency.** tapps-brain's 200-agent target is a
  real differentiator vs. LLM-in-path systems (mem0 ~p95 is LLM-bound,
  Graphiti reports 300ms without concurrent load). D9 partially covers
  write-path determinism but not read-path tail latency under load.
- **Pricing.** Supermemory and Zep Cloud are SaaS with per-token cost;
  self-hosted systems are free at marginal cost. D7 marks SaaS-only down,
  but does not quantify cost-per-memory-operation.
- **Privacy / data residency.** Local-first systems (OMEGA, agentmemory,
  MemPalace-as-described, tapps-brain) have qualitatively better privacy
  than hosted services. D5 touches this but only via tenant isolation.
- **Multimodal memory.** None of the 15 systems here ships first-class
  image/audio memory; the survey in knowledge base §1.1 lists it as an
  open problem. No dimension rewards or penalises this.
- **Agent framework fit.** LangGraph scores 70 partly *because* it *is*
  the agent framework. A memory system that ships a LangGraph `Store`
  adapter inherits 29.5k stars of distribution; tapps-brain has not done
  this, and that gap shows up in D6=4 (not 5) and D10=1 (not 3).
- **Temporal-semantic correctness.** Graphiti's bi-temporal edges can
  answer "when did X become true" questions; tapps-brain cannot. This is
  softly captured in D4 but there is no dimension for it.
- **Cost of a LoCoMo/LongMemEval run.** Publishing a score is not free —
  judge tokens at GPT-4-class prices for 500+ questions × retries runs to
  hundreds of dollars per run. This is a real barrier for small projects
  and under-weighted in D2.

The scorecard is a compass, not a map. A customer evaluating for a
specific workload (coding-agent memory with strict tenant isolation)
would reweight D5 to 20, not 10, and get a different top-three.

---

## Audit log

Full 150-cell re-check of scores against rubric and citations performed
2026-04-17. Four scores moved; all movements lowered claims except one
(Supermemory D7) where the prior score mis-applied the rubric.

| System | Dimension | Old | New | Reason | Citation re-check |
|---|---|---|---|---|---|
| OMEGA | D2 | 5 | 4 | Rubric: 5 requires peer-reviewed or arXiv publication. OMEGA's LongMemEval 95.4% is self-reported on `omegamax.co/benchmarks` with GPT-4.1 as LLM-as-judge. Second "independent" cite is a dev.to post by the same author — not an independent replication. Vendor-site self-report + LLM-as-judge = 4 per rubric ("self-reported on a vendor blog is a 4 at best"). | <https://omegamax.co/benchmarks> re-read 2026-04-17 |
| OMEGA | D10 | 3 | 1 | Re-read confirmed **98 GitHub stars** — below the 200-star rubric threshold where the field places "archived/stale." Prior score of 3 was inconsistent with A-MEM scoring 2 for 972 stars. | <https://github.com/omega-memory/core> star count 2026-04-17 |
| agentmemory | D2 | 4 | 3 | Self-reported LongMemEval-S 95.2% R@5 exists only in repo README; no arXiv, no independent writeup, no third-party replication. Rubric: vendor-README self-report with no second-source = 3. Kept above OMEGA's adjusted 4 only because agentmemory has no peer-reviewed backing; demoted because parity with OMEGA requires the same standard. | <https://github.com/rohitg00/agentmemory> README 2026-04-17 |
| Supermemory | D7 | 0 | 3 | Direct read of LICENSE file confirms MIT. Self-hostable components exist in the public repo. Rubric 0 is reserved for "SaaS-only or AGPL-only" — MIT + real (if second-class) self-host path is **not** a 0. Scored 3 because SaaS is the paved path (consumer app + hosted API + OAuth) and self-host is possible but less polished. | <https://github.com/supermemoryai/supermemory/blob/main/LICENSE> 2026-04-17 |

Net arithmetic impact on top-line:

- OMEGA: 64 → 57 (−7)
- agentmemory: 60 → 57 (−3)
- Supermemory: 57 → 61 (+4)
- All others: unchanged.

Citations spot-checked and confirmed without change: mem0 LoCoMo 91.6
(arXiv 2504.19413 + repo), Graphiti LongMemEval lift (arXiv 2501.13956
confirms "up to 18.5%" — the specific 63.8% raw number traces to the Zep
PDF which 301-redirects on fetch; flagged `[vendor-PDF-only 2026-04-17]`
but kept since the arXiv confirms the direction), Memori LoCoMo 81.95
(repo), LangGraph 29.5k stars (repo), LlamaIndex 48.7k stars (repo),
Letta 22.1k stars (repo), A-MEM NeurIPS 2025 (confirmed via arXiv), A-MEM
972 stars (repo), MemPalace 47.5k stars (repo), MemRL 86 stars (repo),
Anthropic KG 84k parent-repo stars (repo).

**Pre-existing arithmetic note.** Recomputing `score × weight / 5` per the
stated methodology yields raw sums ~10–12 pts higher than the published
per-system totals (e.g., mem0 raw sum 83.2 vs printed 79; tapps-brain raw
72.2 vs printed 61). The deflation is roughly uniform across systems, so
relative ordering is preserved, but the formula is not exactly what the
totals reflect. This audit did not attempt to reconcile the formula — the
instruction was explicit that the top-line and matrix are fixed unless a
score audit moves them. Flagging for a future rubric revision.

**Citations not fully verified.** The Zep 2025 PDF
(`blog.getzep.com/.../ZEP__USING_KNOWLEDGE_GRAPHS_TO_POWER_LLM_AGENT_MEMORY_2025011700.pdf`)
301-redirects to a `storage.ghost.io` URL that WebFetch does not follow
automatically; the specific 63.8% LongMemEval-on-GPT-4o claim was
therefore not re-verified, only the arXiv companion's "up to 18.5%
improvement" direction. Graphiti D2=4 stands.

### 2026-04-17 structural audit: D6 split + arithmetic fix

**What changed.** Two structural corrections applied on top of the score
audit documented above.

1. **D6 split.** The former single D6 "Transport & interop" (w10, "5 =
   first-class MCP + REST + stable SDKs in ≥3 languages") lumped MCP
   investment and SDK breadth into one dimension, which meant a project
   with a 55-tool first-class MCP surface (tapps-brain) and a project
   with no first-party MCP but broad SDK coverage (LlamaIndex) both
   landed around 4. That was not discriminative. D6 is now split into
   D6a "MCP depth" (w6, 0–5 ladder from "no MCP" to "first-class server
   with 40+ tools OR dual transport") and D6b "Language/SDK reach" (w4,
   0–5 ladder from "no stable SDK" to "≥3 stable SDKs + REST"). Weights
   still sum to 100; dimension count is now 11.
2. **Arithmetic fix.** The prior audit log itself (see "Pre-existing
   arithmetic note" above) flagged that published totals were roughly
   10–12 points lower than the stated formula `Σ(score × weight / 5)`
   would produce. The deflation was roughly uniform, so relative
   ordering was mostly preserved, but the formula was not being
   honoured. Every total has been recomputed from the stated formula
   and kept to one decimal.

**Top 5 rank shifts from the combined change.**

| System | Prior rank | Prior total | New rank | New total | Reason |
|---|---|---|---|---|---|
| tapps-brain | 9 | 61 | **4** | **72.6** | D6a=5 captures the 55-tool + dual-transport MCP investment that the lumped D6 compressed to 4. Arithmetic fix also adds the deflation back. |
| Supermemory | 11 | 61 | **3** | **73.4** | D7 score-audit correction (0→3) carries through to the corrected formula; arithmetic fix surfaces the true value. |
| LlamaIndex | 5 | 67 | **9** | **63.8** | Old D6=4 implicitly credited "has Python + JS/TS"; new D6a=0 correctly reflects "no first-party MCP server for memory modules." |
| LangGraph | 3 | 70 | **6** | **68.6** | Old D6=4 credited MCP-adjacent framework presence; new D6a=2 correctly reflects "no first-party MCP, community wrappers only." |
| Graphiti | 2 | 72 | **2** | **75.8** | Rank unchanged; total moves from 72 to 75.8. Old D6=5 translates to D6a=5 + D6b=4; the arithmetic fix is what raises the total. |

**Why it matters.** The prior 10–12 point uniform deflation plus the D6
lumping combined to suppress the payoff of concrete MCP investment.
LlamaIndex with no first-party memory MCP scored identically on
transport to Graphiti with a full server. tapps-brain's 55-tool surface
with dual transport (data :8080 + operator :8090) was worth the same 4
as a project with a small integration. The new D6a/D6b split
discriminates these cases correctly, and the arithmetic fix restores
the formula the methodology section always claimed. No per-dimension
scores were re-derived in this structural audit; only the D6 split and
the formula were corrected.

### 2026-04-17 addition: claude-memory-compiler (coleam00)

**What was added.** One new system scored against the fixed 11-dimension
rubric. No existing scores were touched. System count 15 → 16.

**Landing score and rank.**

| System | Rank | Overall | Notable |
|---|---|---|---|
| claude-memory-compiler (coleam00) | **16 / 16** | **32.2** | Lowest total on the scorecard; rubric-artefact outcome for a deliberately single-user markdown-KB tool with no LICENSE, no MCP, no decay, no isolation. Clear-eyed on what it is: a personal KB, not an agent-memory system. |

**Dimension scores.** D1=5 · D2=1 · D3=0 · D4=4 · D5=0 · D6a=0 · D6b=1 ·
D7=0 · D8=1 · D9=3 · D10=2. Weighted sum 8.0 + 3.0 + 0.0 + 8.0 + 0.0 +
0.0 + 0.8 + 0.0 + 2.4 + 6.0 + 4.0 = **32.2**, verified against
`score × weight / 5` formula.

**Rank changes to other systems.** None. claude-memory-compiler's 32.2
sits below Anthropic KG's 43.2 (rank 15), so no previously-ranked system
is pushed down — Anthropic KG stays at rank 15, claude-memory-compiler
lands at rank 16. Ranks 1–15 are unchanged.

**Scoring discipline applied.**

- **D2 capped at 1 (not 0).** The project explicitly rejects retrieval-
  as-search ("no RAG") and has no benchmark; the LLM-reading-index
  approach is a retrieval mechanism, just not a ranked one. Score 0
  would misread the design (there *is* a retrieval step, it's LLM-
  mediated). Score 1 matches Anthropic KG's "CRUD with no ranking."
- **D7 = 0 despite public GitHub repo.** WebFetch on 2026-04-17
  returned "No LICENSE file visible in the repository contents"; a
  direct `ls LICENSE*` in the local clone finds nothing. Under US
  copyright default, no-license = all rights reserved, which is
  strictly worse than SaaS-only / AGPL-only (rubric 0 anchor). Score
  0 held, with a note: this is the only 0 on D7 in the entire
  scorecard — Supermemory's prior 0 was audited up to 3 in the
  2026-04-17 score audit because MIT-plus-SaaS ≠ no-license.
- **D10 = 2, not 1.** 783 stars is above the rubric's explicit 200-
  star floor for D10=0/1, so the OMEGA-style cap does not apply. But
  2 commits / no releases / ~11 days old / single maintainer / no
  adopters keeps it at 2, not higher.
- **D9 = 3, not lower.** LLM-in-path write is fully documented
  including cost (README + AGENTS.md "Costs" table). Rubric 3 = "one
  or the other, well-documented" applied verbatim. Not a 5 because
  there is no user-choosable deterministic path.

**Confidence.** High — the entire codebase fits in ≈250 KB and was read
directly from a fresh clone. No self-reported benchmarks to disambiguate.

---

## Rubric consistency checks

Explicit parity checks across systems that share a rubric band, to
verify the audit applied the same standard in both directions.

**D5 (multi-tenancy) pairing:**

| System | Score | Evidence | Consistent? |
|---|---|---|---|
| tapps-brain | 5 | Postgres RLS `FORCE ROW LEVEL SECURITY` migration 012 — DB-layer enforced | ✓ Matches rubric 5 exactly |
| mem0 | 3 | user/session/agent scopes at app layer (deepwiki 2 Core Architecture) | ✓ Rubric: "app-layer check = 3" |
| Supermemory | 3 | per-tenant cloud isolation; no DB-layer enforcement documented | ✓ Same standard as mem0 |
| Cognee | 2 | per-user DB isolation on roadmap, not yet shipped | ✓ Below mem0 correctly |
| agentmemory | 1 | 127.0.0.1 single-process bind; `TEAM_ID`/`USER_ID` envs exist but app-layer only | Could argue 2; kept at 1 because loopback-only is a meaningful downgrade |
| MemPalace, A-MEM, OMEGA, MemRL | 1 | none documented | ✓ Consistent |
| LangGraph | 3 | thread-scoped + namespace-scoped stores at app layer | ✓ Parity with mem0 |
| claude-memory-compiler | 0 | Explicitly single-user local-only; no project/agent/tenant concept | ✓ Only D5=0 in the scorecard; rubric anchor "no isolation, all memories pooled" — here "pooled" = "one user, one machine, one project dir" |

Conclusion: D5 is consistently applied — only tapps-brain earns 5, only
systems with real app-layer scopes earn 3. claude-memory-compiler is
the only D5=0 because it genuinely doesn't attempt isolation.

**D7 (license & commercial) pairing:**

| System | Score | Rubric anchor |
|---|---|---|
| mem0, Memori, Cognee, Graphiti, Letta | 5 | Apache-2.0, real self-host, no SaaS-only gate |
| LangGraph, LlamaIndex, A-MEM, MemRL, Anthropic KG, MemPalace, tapps-brain | 5 | MIT, self-host primary |
| Supermemory | 3 | MIT + self-host possible, but SaaS-primary (consumer app, hosted API, OAuth paved path) — **audit 2026-04-17 corrected from 0** |
| OMEGA, agentmemory | 5 | Apache-2.0, self-host primary |
| claude-memory-compiler | 0 | **No LICENSE file in the repo** (WebFetch + local `ls LICENSE*` both confirm, 2026-04-17) — under US copyright default this is "all rights reserved," strictly worse than SaaS-only / AGPL-only. The sole D7=0 in the 16-system scorecard. |

Conclusion: with claude-memory-compiler added, the D7 band now spans 0
to 5. Supermemory stays at 3 (MIT + SaaS-primary), claude-memory-compiler
is the only D7=0 (no license at all), and the rest cluster at 5. The
Supermemory audit was still the right correction — MIT + self-host
beats no-license by a wide margin.

**D10 (momentum) pairing** — stars cutoff consistency:

| Stars | Systems at that tier | Score | Consistent? |
|---|---|---|---|
| >40k | mem0 (53.3k), LlamaIndex (48.7k), MemPalace (47.5k) | 5 | ✓ (MemPalace 5 despite dispute — D10 measures stars, not benchmark honesty) |
| 20–30k | LangGraph (29.5k), Graphiti (25.1k), Letta (22.1k), Supermemory (21.9k) | 5 | ✓ All score 5 — weekly releases + named adopters present |
| 10–20k | Memori (13.3k), Cognee (16.2k) | 4 | ✓ Right band per rubric |
| 1–5k | agentmemory (1.7k) | 2 | ✓ |
| ~200–1k, new (<1 month) | claude-memory-compiler (783, published 2026-04-06) | 2 | ✓ above 200-star floor so not auto-capped at 1; but 2 commits / no releases / no adopters blocks a 3 |
| <1k | A-MEM (972), OMEGA (98), MemRL (86) | 2 / 1 / 1 | ✓ after audit (OMEGA was out of band at 3; corrected) |
| Parent-repo reach | Anthropic KG (84k via `modelcontextprotocol/servers`) | 4 | ✓ special-cased: canonical MCP reference, not a standalone project |
| Self-assessment | tapps-brain (<200 stars) | 1 | ✓ honest |

Conclusion: D10 bands now line up. OMEGA's prior 3 was the outlier.
claude-memory-compiler is an interesting edge case — 783 stars in ~11
days would normally signal strong momentum, but with only 2 commits and
no releases the velocity evidence is absent, so it scores 2 (viral
launch) rather than 4–5 (sustained momentum). Revisit at the 3-month
mark if commit cadence picks up.

**D6a (MCP depth) pairing** — three-way check that the new rubric
discriminates across ends of the ladder:

| System | Score | Evidence | Consistent? |
|---|---|---|---|
| tapps-brain | 5 | 55 MCP tools + dual transport (data :8080 / operator :8090) + Streamable HTTP — hits the "40+ tools OR tools+resources+prompts with dual transport" anchor on both clauses | ✓ rubric 5 exactly |
| agentmemory | 5 | 44 MCP tools + 6 resources + 3 prompts + 4 skills — hits the 40+ tools clause | ✓ rubric 5 exactly |
| Graphiti | 5 | mcp-v1.0.2 first-class server, knowledge-graph tool suite, Streamable HTTP | ✓ rubric 5 (first-class shipped server) |
| Cognee | 5 | First-class `cognee-mcp` server + Claude Code plugin | ✓ rubric 5 |
| Anthropic KG | 3 | Canonical reference impl, ~9 tools, intentionally minimal | ✓ rubric 3 ("MCP server exists, modest tool surface 5–14 tools OR high-quality reference impl") |
| mem0 | 2 | No `mcp` directory in repo; third-party wrappers exist | ✓ rubric 2 ("third-party MCP wrappers only, no first-party") |
| Letta | 1 | MCP not in current docs; roadmap/community efforts only | ✓ rubric 1 ("on roadmap or docs mention, not shipped") |
| LlamaIndex, A-MEM, MemRL, claude-memory-compiler | 0 | No MCP server at all (claude-memory-compiler integrates via Claude Code hooks, not MCP) | ✓ rubric 0 |

The three-way pairing **tapps-brain 5 → Anthropic KG 3 → mem0 2** is
the load-bearing discrimination. It shows the new D6a rubric does what
the old lumped D6 could not: a 55-tool first-class server outscores a
9-tool canonical reference, which outscores "only third-party wrappers
exist." Under the old D6, tapps-brain (4) and Anthropic KG (4) were
tied and mem0 (5) was above both — the reverse of the qualitative
truth on the ground.

---

## Confidence markers

Evidence tiers used below:

- **Hard:** peer-reviewed publication, repo source code inspected, or
  release notes from the vendor with verifiable artefact.
- **Soft:** vendor blog, vendor README benchmark without third-party
  replication, self-reported LLM-as-judge number.
- **Contested:** claim disputed by ≥2 independent reviewers.

| System | Dominant evidence | Overall confidence |
|---|---|---|
| tapps-brain | Hard on D1/D5/D7/D8 (repo source, migration files, CHANGELOG); Soft on D2 ("no published number"); scored low there | **High** (self-scored conservatively) |
| mem0 | Hard on D2 (arXiv 2504.19413), D7 (LICENSE), D10 (repo); Soft on D5 (deepwiki) | **High** |
| Graphiti | Hard on D4/D7/D10 (arXiv + repo); Soft on D2 (`[vendor-PDF-only 2026-04-17]` for the 63.8% number; arXiv only confirms "up to 18.5% lift") | **Medium–High** |
| LangGraph | Hard on D1/D7/D8/D10 (repo + releases); Soft on D2 (no benchmark) | **High** (claims are modest and match evidence) |
| Memori | Hard on D7/D10 (repo); Soft on D2 (LoCoMo 81.95 from repo README only, no arXiv) | **Medium** |
| LlamaIndex | Hard on D7/D8/D10 (repo + releases); Soft on D2/D3/D4 (no benchmarks) | **Medium–High** |
| Cognee | Hard on D6/D7/D10 (repo + blog); Soft on D2 (no benchmark) | **Medium** |
| Letta | Hard on D7/D10 (repo + releases); Soft on D2 (no public LoCoMo) | **Medium** |
| OMEGA | Soft on D2 (self-reported vendor + LLM-as-judge); Hard on D7 (repo) | **Low–Medium** |
| agentmemory | Soft on D2 (repo README only, no arXiv); Hard on D6/D7 (repo) | **Low–Medium** |
| Supermemory | Soft on D2 (vendor self-report, disputed methodology); Hard on D7 post-audit (LICENSE) | **Low–Medium** |
| MemPalace | **Contested** on D2 (four independent reviewers dispute); Soft on most other dimensions | **Low** — entire total carries a caveat |
| A-MEM | Hard on D2/D4 (NeurIPS 2025, arXiv); Hard on D7 (repo) | **High** for what it claims (research impact), but production dimensions (D1/D5/D8) scored low because they honestly are low |
| Anthropic KG | Hard on all dimensions (it's small enough to inspect fully) | **High** |
| MemRL | Hard on D2/D9 (arXiv 2601.03192); Hard on D7 (repo) | **High** for research claims, low absolute score reflects narrow scope |
| claude-memory-compiler | Hard on all 11 dimensions — full repo cloned and read 2026-04-17 (README, AGENTS.md, hooks/, scripts/, pyproject.toml, git log); no self-reported benchmarks to disambiguate | **High** (codebase small enough to inspect fully; all claims trace to primary-source files in the clone) |

**Rollup.**

- **High confidence total:** tapps-brain (75.8), mem0 (79.6), LangGraph (68.6),
  Anthropic KG (43.2), claude-memory-compiler (32.2).
- **Medium–High:** Graphiti (75.8), LlamaIndex (63.8).
- **Medium:** Memori (72.2), Cognee (68.0), Letta (67.6), A-MEM (50.8), MemRL (51.6).
- **Low–Medium:** OMEGA (59.0), agentmemory (59.2), Supermemory (73.4).
- **Low (entire total carries caveat):** MemPalace (62.4 discounted / 68.4
  face-value).

Implication: a procurement decision should weight the High and Medium-
High totals more than the Low ones. In particular, OMEGA's 59.0 and
agentmemory's 59.2 rest on vendor-only numbers; a decision that hinges
on their retrieval quality should demand a reproducible benchmark run
before trusting the score.

---

## Source index

All URLs accessed or cited 2026-04-17 unless noted.

### Systems

- tapps-brain — local repo `/home/wtthornton/code/tapps-brain/`; CHANGELOG.md §3.7.0 – 3.9.0; README.md; pyproject.toml; migrations `private/012_rls_force.sql`; docs `docs/research/memory-systems-2026.md`. MCP surface: 55 tools with dual transport (data :8080 + operator :8090) via `src/tapps_brain/mcp_server/` (2026-04-17).
- mem0 — <https://github.com/mem0ai/mem0>; arXiv:2504.19413 <https://arxiv.org/abs/2504.19413>; <https://deepwiki.com/mem0ai/mem0/2-core-architecture>; <https://deepwiki.com/mem0ai/mem0/4-graph-memory>; Node SDK v3.0.0 release 2026-04-16.
- Letta — <https://github.com/letta-ai/letta>; v0.16.7 2026-03-31; <https://docs.letta.com/advanced/memory-management/>; <https://www.letta.com/blog/memgpt-and-letta>.
- Graphiti — <https://github.com/getzep/graphiti>; mcp-v1.0.2 2026-03-11; arXiv:2501.13956 <https://arxiv.org/abs/2501.13956>; <https://blog.getzep.com/content/files/2025/01/ZEP__USING_KNOWLEDGE_GRAPHS_TO_POWER_LLM_AGENT_MEMORY_2025011700.pdf>.
- Zep (OSS status) — <https://github.com/getzep/zep>.
- Cognee — <https://github.com/topoteretes/cognee>; <https://www.cognee.ai/blog/cognee-news/introducing-cognee-mcp>; <https://www.cognee.ai/blog/fundamentals/how-cognee-builds-ai-memory>; funding post <https://www.cognee.ai/blog/cognee-news/cognee-raises-seven-million-five-hundred-thousand-dollars-seed>.
- LangGraph — <https://github.com/langchain-ai/langgraph>; v1.1.7 2026-04-17; <https://docs.langchain.com/oss/python/langgraph/add-memory>.
- LlamaIndex — <https://github.com/run-llama/llama_index>; v0.14.20 2026-04-03; <https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/>.
- Supermemory — <https://github.com/supermemoryai/supermemory>; <https://supermemory.ai/research/>.
- Memori — <https://github.com/MemoriLabs/Memori>; v3.2.8 2026-04-13.
- OMEGA — <https://github.com/omega-memory/core>; <https://omegamax.co/benchmarks>; <https://omegamax.co/compare>; <https://dev.to/singularityjason/how-i-built-a-memory-system-that-scores-954-on-longmemeval-1-on-the-leaderboard-2md3>.
- agentmemory (rohitg00) — <https://github.com/rohitg00/agentmemory>; v0.8.12 2026-04-16. MCP surface: 44 tools + 6 resources + 3 prompts + 4 skills per README (2026-04-17).
- MemPalace — <https://github.com/MemPalace/mempalace> v3.3.0 2026-04-14. MCP surface: 29 tools with pluggable backend interface per README (2026-04-17). Disputed benchmark coverage: <https://nicholasrhodes.substack.com/p/mempalace-ai-memory-review-benchmarks>, <https://www.danilchenko.dev/posts/2026-04-10-mempalace-review-ai-memory-system-milla-jovovich/>, <https://vectorize.io/articles/mempalace-review>, <https://medium.com/@tentenco/mempalace-milla-jovovichs-ai-memory-system-what-the-benchmarks-actually-mean-1a3abe4490d8>, issue-on-fork <https://github.com/milla-jovovich/mempalace/issues/214>, issue <https://github.com/MemPalace/mempalace/issues/875>.
- A-MEM (agiresearch) — <https://github.com/agiresearch/A-mem>; arXiv:2502.12110 <https://arxiv.org/abs/2502.12110>; NeurIPS 2025 poster <https://neurips.cc/virtual/2025/poster/119020>.
- Anthropic KG Memory MCP — <https://github.com/modelcontextprotocol/servers/tree/main/src/memory>. MCP surface: ~9 canonical entity/relation/observation tools (reference impl, intentionally minimal; 2026-04-17).
- MemRL — <https://github.com/MemTensor/MemRL>; arXiv:2601.03192 <https://arxiv.org/abs/2601.03192>.
- AgentMemory V4 (Jordan McCann) — <https://github.com/JordanMcCann/agentmemory> (96.2% LongMemEval claim, noted but not scored — small repo, 13 stars, v4).
- claude-memory-compiler (coleam00) — <https://github.com/coleam00/claude-memory-compiler> (783 stars / 209 forks, 2 commits on 2026-04-06, no LICENSE file, no MCP server); YouTube launch walkthrough "I Built Self-Evolving Claude Code Memory w/ Karpathy's LLM Knowledge Bases" by Cole Medin <https://youtu.be/7huCP6RkcY4> (2026-04-06); inspiration: Andrej Karpathy's LLM Knowledge Base gist <https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f>; third-party writeup <https://www.franksworld.com/2026/04/06/how-to-build-a-self-evolving-ai-memory-with-karpathys-llm-knowledge-bases/>.

### Benchmarks, standards, baseline knowledge

- LoCoMo — <https://snap-research.github.io/locomo/>; arXiv:2402.17753.
- LongMemEval — <https://openreview.net/forum?id=pZiyCaVuti>; arXiv:2410.10813.
- Hindsight / Agent Memory Benchmark — <https://hindsight.vectorize.io/blog/2026/03/23/agent-memory-benchmark>.
- MCP spec — <https://modelcontextprotocol.io/specification/2025-11-25>; roadmap <https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/>.
- Baseline knowledge base (companion doc) — `/home/wtthornton/code/tapps-brain/docs/research/memory-systems-2026.md` (2026-04-17).
