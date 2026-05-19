---
title: tapps-brain Architecture Diagrams
description: C4 + ER + class hierarchy + sequence flows for the tapps-brain memory system. Code-aligned, generated from the live tree.
tags: [architecture, c4, diagrams, mermaid, reference]
content_type: reference
---

# tapps-brain — Architecture Diagrams

Code-aligned visual reference. Every diagram below is generated from the current `src/tapps_brain/` tree or hand-authored against current call-site code. Sources of truth:

- [src/tapps_brain/](../../src/tapps_brain/) — the code
- [docs/engineering/system-architecture.md](system-architecture.md) — the narrative
- [docs/engineering/call-flows.md](call-flows.md) — the request flows
- [docs/architecture.html](../architecture.html) — interactive pan/zoom/toggle viewer (open in browser)
- [docs/engineering/architecture-report.html](architecture-report.html) — full per-package report with SVG flows

## Refresh procedure

```bash
# All five regenerate from current code:
docs_generate_interactive_diagrams(
  diagram_types="dependency,module_map,c4_context,c4_container,c4_component,er_diagram",
  output_path="docs/architecture.html", motion="subtle"
)
docs_generate_architecture(output_path="docs/engineering/architecture-report.html", motion="subtle")
docs_generate_diagram(diagram_type="er_diagram", scope="src/tapps_brain/models.py")
docs_generate_diagram(diagram_type="class_hierarchy", scope="src/tapps_brain/errors.py")
docs_generate_diagram(diagram_type="c4_component", scope="src/tapps_brain/mcp_server")
```

When code changes substantially, re-run all five and commit the regenerated `*.html` outputs alongside this `diagrams.md` update.

---

## 1. C4 — System Context

Who talks to tapps-brain and why. The brain is one Docker container fronted by HTTP + MCP; agents never touch Postgres directly.

```mermaid
C4Context
    title tapps-brain — System Context

    Person(coding_agent, "Coding Agent", "Claude Code, Cursor, AgentForge agent, Ralph loop")
    Person(operator, "Operator", "Human SRE / dev — uses CLI for maintenance, migrations, audits")

    System_Boundary(deployment, "tapps-brain deployment") {
      System(tapps_brain, "tapps-brain", "Persistent memory + Knowledge Graph<br/>HTTP :8080 (/mcp + /v1/*)<br/>Operator MCP :8090")
    }

    SystemDb_Ext(postgres, "PostgreSQL 17 + pgvector", "All durable state: private memory,<br/>Hive, Federation, KG, audit, sessions")
    System_Ext(context7, "Context7 docs API", "External library doc lookups for<br/>doc_validation + LLM judge")
    System_Ext(otel, "OpenTelemetry collector", "Traces + GenAI metrics<br/>(optional [otel] extra)")
    System_Ext(agentforge, "AgentForge BrainBridge", "Downstream consumer via<br/>tapps_brain_client wheel (vendored)")

    Rel(coding_agent, tapps_brain, "remember / recall / record_event /<br/>get_neighbors / explain_connection", "MCP Streamable HTTP")
    Rel(coding_agent, tapps_brain, "REST: /v1/remember /v1/recall /v1/healthz", "HTTPS + Bearer token")
    Rel(operator, tapps_brain, "maintenance, migrations,<br/>health, backup, GC", "tapps-brain CLI →<br/>operator MCP :8090")

    Rel(tapps_brain, postgres, "TAPPS_BRAIN_DATABASE_URL<br/>psycopg async pool", "TCP 5432")
    Rel(tapps_brain, context7, "library doc lookups<br/>(doc_validation)", "HTTPS")
    Rel(tapps_brain, otel, "spans + metrics<br/>(GenAI semconv v1.35)", "OTLP/HTTP")
    Rel(agentforge, tapps_brain, "BrainBridge mediates<br/>all agent calls", "HTTP")

    UpdateRelStyle(coding_agent, tapps_brain, $offsetY="-30", $offsetX="-50")
```

**Read this as:** the dashed lines (external) are out-of-process; the box around `tapps-brain` is one container that mounts both `/mcp` and `/v1/*` on port 8080 plus a separate operator MCP on 8090. All durable state goes to one Postgres unless `TAPPS_BRAIN_HIVE_DSN` / `TAPPS_BRAIN_FEDERATION_DSN` are set to split.

References: [CLAUDE.md — Key environment variables](../../CLAUDE.md), [docs/guides/deployment.md](../guides/deployment.md), [docs/guides/agentforge-integration.md](../guides/agentforge-integration.md).

---

## 2. C4 — Container

How a request lands inside the container and reaches Postgres.

```mermaid
C4Container
    title tapps-brain — Container View

    Person(agent, "Coding Agent", "Claude Code / Cursor / AgentForge")

    Container_Boundary(brain, "tapps-brain-http (Docker)") {
      Container(http_adapter, "http_adapter.py", "FastAPI ASGI", "Bearer auth, Origin allow-list,<br/>traceparent, /healthz, /metrics,<br/>OpenAPI contract")
      Container(rest_routes, "/v1/* REST routes", "FastAPI routers", "remember, recall, reinforce,<br/>tools/list, healthz")
      Container(mcp_streamable, "/mcp Streamable HTTP", "FastMCP", "tools_brain, tools_memory,<br/>tools_kg, tools_hive,<br/>tools_feedback, tools_resources")
      Container(profile_gate, "RestProfileGate + ToolFilter", "ASGI middleware +<br/>FastMCP wrapper", "X-Brain-Profile enforcement,<br/>deferred-tool hiding (TAP-1985)")
      Container(services, "services/*", "Pure functions", "memory_service, kg_service,<br/>hive_service, feedback_service,<br/>flywheel_service, ...")
      Container(store, "MemoryStore", "Sync + AsyncMemoryStore", "In-memory cache +<br/>write-through to Postgres,<br/>safety / write_policy / consolidation")
      Container(retriever, "MemoryRetriever", "BM25 + pgvector + RRF", "Hybrid search with optional<br/>FlashRank cross-encoder rerank")
      Container(kg_store, "PostgresKnowledgeGraphStore", "Sync + Async", "Entities, edges, evidence,<br/>FSRS-like edge decay")
      Container(experience, "ExperienceEventRecorder", "Single TX writer", "Atomic event + memory + KG +<br/>evidence in one transaction")
      Container(backends, "PostgresPrivateBackend,<br/>PostgresHiveBackend,<br/>PostgresFederationBackend", "psycopg pools", "Per-tenant RLS,<br/>tsvector + HNSW,<br/>LISTEN/NOTIFY")
    }

    Container_Boundary(operator_path, "tapps-brain-operator-mcp :8090") {
      Container(operator_mcp, "operator server", "FastMCP", "GC, consolidation, migrations,<br/>relay import, restore_hive")
    }

    ContainerDb(postgres, "PostgreSQL 17 + pgvector", "tapps-brain-db", "private_memories, hive_*,<br/>federation_*, kg_entities,<br/>kg_edges, evidence,<br/>session_chunks, audit_log,<br/>experience_events, idempotency_keys")

    Rel(agent, http_adapter, "POST /mcp or /v1/*", "HTTPS + Bearer")
    Rel(http_adapter, profile_gate, "request", "ASGI")
    Rel(profile_gate, rest_routes, "if /v1/*", "")
    Rel(profile_gate, mcp_streamable, "if /mcp", "")
    Rel(rest_routes, services, "call", "")
    Rel(mcp_streamable, services, "thin wrappers", "")
    Rel(services, store, "save / get / recall", "")
    Rel(services, kg_store, "record_event / neighbors", "")
    Rel(services, experience, "atomic write", "")
    Rel(store, retriever, "search", "")
    Rel(store, backends, "write-through", "")
    Rel(retriever, backends, "hybrid query", "")
    Rel(experience, backends, "single TX", "")
    Rel(experience, kg_store, "same TX", "")
    Rel(backends, postgres, "psycopg pool", "TCP 5432")
    Rel(kg_store, postgres, "psycopg pool", "TCP 5432")

    Rel(agent, operator_mcp, "operator-only", "Separate token")
    Rel(operator_mcp, services, "maintenance fns", "")
```

References: [src/tapps_brain/http_adapter.py](../../src/tapps_brain/http_adapter.py), [src/tapps_brain/mcp_server/server.py](../../src/tapps_brain/mcp_server/server.py), [src/tapps_brain/services/](../../src/tapps_brain/services/), [src/tapps_brain/store.py](../../src/tapps_brain/store.py).

---

## 3. C4 — Component (MCP server)

The MCP server is a thin wrapper layer over the service functions; each `tools_*` module registers a family of tools.

```mermaid
C4Component
    title MCP Server — Components

    Container_Boundary(mcp_server, "src/tapps_brain/mcp_server/") {
      Component(server, "server.py", "FastMCP wiring", "create_server, main entry points")
      Component(context, "context.py", "Per-request state", "REQUEST_PROJECT_ID, REQUEST_AGENT_ID,<br/>REQUEST_SCOPE, REQUEST_PROFILE, _StoreCache")
      Component(profile_resolver, "profile_resolver.py", "Per-request profile", "Header → registry → env precedence")
      Component(profile_registry, "profile_registry.py", "YAML-backed profiles", "Tool name allow-list per profile,<br/>defer_loading hints (TAP-1985)")
      Component(tool_filter, "tool_filter.py", "tools/list + tools/call gate", "Hides + rejects out-of-profile tools,<br/>STORY-073.4 counters")

      Component(tools_brain, "tools_brain.py", "Agent Brain tools", "brain_remember, brain_recall,<br/>brain_forget, brain_learn_success,<br/>brain_learn_failure, brain_status")
      Component(tools_memory, "tools_memory.py", "Memory tools", "memory_save, memory_get, memory_search,<br/>memory_supersede, memory_relations,<br/>memory_audit, memory_capture, ...")
      Component(tools_kg, "tools_kg.py", "Knowledge-Graph tools", "brain_record_event,<br/>brain_record_events_batch,<br/>brain_get_neighbors,<br/>brain_explain_connection,<br/>brain_record_feedback")
      Component(tools_hive, "tools_hive.py", "Hive tools", "hive_status, hive_search,<br/>hive_propagate, hive_push")
      Component(tools_feedback, "tools_feedback.py", "Feedback + flywheel", "feedback_rate, feedback_gap,<br/>feedback_issue, diagnostics_*,<br/>flywheel_*")
      Component(tools_maintenance, "tools_maintenance.py", "Operator-only", "consolidate, gc, migrate,<br/>backup_hive, restore_hive,<br/>relay_import")
      Component(tools_resources, "tools_resources.py", "Resources + prompts", "memory:// resources,<br/>workflow prompts")
      Component(tools_agents, "tools_agents.py", "Agent registry", "agent_register, agent_list, agent_delete")
    }

    Container(services, "services/*", "Pure functions", "All tool bodies live here")

    Rel(server, profile_registry, "loads")
    Rel(server, profile_resolver, "wires")
    Rel(server, tool_filter, "installs")
    Rel(tool_filter, profile_resolver, "reads profile")
    Rel(tool_filter, profile_registry, "checks allow-list")

    Rel(tools_brain, services, "delegates")
    Rel(tools_memory, services, "delegates")
    Rel(tools_kg, services, "delegates")
    Rel(tools_hive, services, "delegates")
    Rel(tools_feedback, services, "delegates")
    Rel(tools_maintenance, services, "delegates")

    Rel(tools_brain, context, "resolves store")
    Rel(tools_memory, context, "resolves store")
    Rel(tools_kg, context, "resolves store")
```

References: [src/tapps_brain/mcp_server/](../../src/tapps_brain/mcp_server/), [docs/planning/epics/EPIC-073.md](../planning/epics/EPIC-073.md).

---

## 4. C4 — Component (Services layer)

Pure service functions — transport-agnostic. Every `@mcp.tool` and REST route ultimately calls one of these.

```mermaid
C4Component
    title Services Layer — Components (Container_Boundary services)

    Container_Boundary(services, "src/tapps_brain/services/") {
      Component(memory_service, "memory_service.py", "54 functions", "brain_remember, brain_recall, memory_save,<br/>memory_supersede, memory_audit, async_*")
      Component(kg_service, "kg_service.py", "11 functions", "record_event, record_events_batch,<br/>get_neighbors, explain_connection,<br/>record_kg_feedback")
      Component(hive_service, "hive_service.py", "6 functions", "hive_status, hive_search, hive_propagate,<br/>hive_push, hive_write_revision,<br/>hive_wait_write")
      Component(feedback_service, "feedback_service.py", "5 functions", "rate, gap, issue, record, query")
      Component(flywheel_service, "flywheel_service.py", "5 functions", "process, gaps, report, evaluate,<br/>hive_feedback")
      Component(diagnostics_service, "diagnostics_service.py", "3 functions", "report, history, health")
      Component(maintenance_service, "maintenance_service.py", "4 functions", "consolidate, gc, stale, session_end")
      Component(profile_service, "profile_service.py", "3 functions", "info, onboarding, switch")
      Component(agents_service, "agents_service.py", "4 functions", "register, create, list, delete")
      Component(relay_service, "relay_service.py", "1 function", "relay_export")
    }

    Container(store, "MemoryStore", "Sync + Async")
    Container(retriever, "MemoryRetriever", "BM25 + vector + RRF")
    Container(experience, "ExperienceEventRecorder", "Atomic write")
    Container(kg_backend, "KnowledgeGraphStore", "Postgres KG")
    Container(hive_backend, "HiveBackend", "Postgres hive")
    Container(feedback_store, "FeedbackStore", "Postgres feedback")

    Rel(memory_service, store, "calls")
    Rel(memory_service, retriever, "via store")
    Rel(kg_service, kg_backend, "calls")
    Rel(kg_service, experience, "for atomic writes")
    Rel(hive_service, hive_backend, "calls")
    Rel(feedback_service, feedback_store, "calls")
    Rel(flywheel_service, feedback_store, "reads")
    Rel(flywheel_service, store, "writes confidence")
    Rel(diagnostics_service, store, "reads")
    Rel(maintenance_service, store, "writes")
```

---

## 5. Entity-Relationship — Pydantic / dataclass models

Generated from [src/tapps_brain/models.py](../../src/tapps_brain/models.py). `MemoryEntry` is the spine — every persisted row maps to one.

```mermaid
erDiagram
    MemoryEntry {
        string key
        string value
        string tier
        float confidence
        string source
        string source_agent
        string scope
        string tags
        string created_at
        string updated_at
        string last_accessed
        int access_count
        string branch
        string last_reinforced
        int reinforce_count
        boolean contradicted
        string contradiction_reason
        string seeded_from
        float embedding
        string embedding_model_id
        string agent_scope
        string memory_group
        string valid_at
        string invalid_at
        string superseded_by
        string valid_from
        string valid_until
        string integrity_hash
        int integrity_hash_v
        string source_session_id
        string source_channel
        string source_message_id
        string triggered_by
        float stability
        float difficulty
        int useful_access_count
        int total_access_count
        float positive_feedback_count
        float negative_feedback_count
        string temporal_sensitivity
        string failed_approaches
        string status
        string stale_reason
        string stale_date
        string memory_class
    }
    KGEntityView {
        string entity_id
        string surface
        float confidence
        string reason
    }
    KGEdgeView {
        string edge_id
        string predicate
        string neighbor_id
        string entity_type
        string canonical_name
        int hop
        float score
        float edge_confidence
        int evidence_count
    }
    KGEvidenceView {
        string evidence_id
        string quote
        string source_uri
        string source_type
        float confidence
    }
    RecallDiagnostics {
        string empty_reason
        int retriever_hits
        int visible_entries
        int mentions_matched
        int mentions_unmatched
        int graph_hits
        int dropped_stale
        int dropped_low_confidence
        float top_score
        float oldest_returned_age_days
    }
    RecallResult {
        string memory_section
        string memories
        int token_count
        float recall_time_ms
        boolean truncated
        int memory_count
        int hive_memory_count
        string quality_warning
        string recall_diagnostics
        string entities
        string edges
        string evidence
    }
    MemorySnapshot {
        string project_root
        string entries
        int total_count
        string tier_counts
        string exported_at
    }
    AgentRegistration {
        string id
        string name
        string profile
        string skills
        string project_root
    }
    RecallResult ||--o{ RecallDiagnostics : "has"
    RecallResult ||--o{ KGEntityView : "has"
    RecallResult ||--o{ KGEdgeView : "has"
    RecallResult ||--o{ KGEvidenceView : "has"
    MemorySnapshot ||--o{ MemoryEntry : "has"
```

For the wire-shape Postgres schema (DDL, indexes, RLS), see [docs/engineering/data-stores-and-schema.md](data-stores-and-schema.md).

---

## 6. Error taxonomy — class hierarchy

Stable wire taxonomy ([src/tapps_brain/errors.py](../../src/tapps_brain/errors.py)) layered on top of the semantic SDK hierarchy ([src/tapps_brain/exceptions.py](../../src/tapps_brain/exceptions.py)).

```mermaid
classDiagram
    class TaxonomyError {
        +error_code: ErrorCode
        +http_body() dict
        +mcp_data() dict
        +http_status() int
        +jsonrpc_code() int
        +retry() RetryPolicy
    }
    class BrainDegradedError {
        error=brain_degraded, http=503, retry=safe
    }
    class BrainRateLimitedError {
        error=brain_rate_limited, http=429, retry=backoff
    }
    class ProjectNotFoundError {
        error=project_not_registered, http=403, retry=never
    }
    class InvalidRequestError {
        error=invalid_request, http=400, retry=never
    }
    class IdempotencyConflictError {
        error=idempotency_conflict, http=409, retry=never
    }
    class NotFoundError {
        error=not_found, http=404, retry=never
    }
    class InternalError {
        error=internal_error, http=500, retry=safe-once
    }

    TaxonomyError <|-- BrainDegradedError
    TaxonomyError <|-- BrainRateLimitedError
    TaxonomyError <|-- ProjectNotFoundError
    TaxonomyError <|-- InvalidRequestError
    TaxonomyError <|-- IdempotencyConflictError
    TaxonomyError <|-- NotFoundError
    TaxonomyError <|-- InternalError

    class TappsBrainError {
        Root SDK exception
    }
    class TappsBrainTransportError {
        Network / DNS / TLS / timeout
    }
    class TappsBrainAuthError {
        401 / 403 + ProjectNotFound
    }
    class TappsBrainTransientError {
        429 + 5xx, retry-safe
    }
    class TappsBrainNotFoundError {
        404
    }
    class TappsBrainValidationError {
        400 / 409 / 422
    }

    TappsBrainError <|-- TappsBrainTransportError
    TappsBrainError <|-- TappsBrainAuthError
    TappsBrainError <|-- TappsBrainTransientError
    TappsBrainError <|-- TappsBrainNotFoundError
    TappsBrainError <|-- TappsBrainValidationError

    TappsBrainTransientError <|-- BrainDegradedError
    TappsBrainTransientError <|-- BrainRateLimitedError
    TappsBrainAuthError <|-- ProjectNotFoundError
    TappsBrainValidationError <|-- InvalidRequestError
    TappsBrainValidationError <|-- IdempotencyConflictError
    TappsBrainNotFoundError <|-- NotFoundError
    TappsBrainError <|-- InternalError
```

**Catch by intent.** Match on `TappsBrainAuthError` (fix token, stop), `TappsBrainTransientError` (the SDK retried already — escalate), `TappsBrainTransportError` (degrade gracefully). The wire `ErrorCode` enum and `RetryPolicy` are documented in full in [docs/guides/errors.md](../guides/errors.md).

---

## 7. Sequence — `brain_recall` end-to-end

How an agent question turns into ranked memories. Hand-authored against [services/memory_service.py::brain_recall](../../src/tapps_brain/services/memory_service.py), [recall.py::RecallOrchestrator](../../src/tapps_brain/recall.py), [retrieval.py::MemoryRetriever](../../src/tapps_brain/retrieval.py), and [fusion.py](../../src/tapps_brain/fusion.py).

```mermaid
sequenceDiagram
    autonumber
    participant Agent as Coding Agent
    participant HTTP as http_adapter (FastAPI)
    participant MW as Middleware<br/>(Origin, Tenant, Profile gate)
    participant MCP as /mcp (FastMCP)
    participant TF as tool_filter
    participant Tool as tools_brain.brain_recall
    participant Ctx as context._StoreCache
    participant Svc as services.memory_service.brain_recall
    participant Store as MemoryStore.recall
    participant Retr as MemoryRetriever
    participant BM as BM25Scorer
    participant Vec as pgvector HNSW
    participant Fus as fusion.reciprocal_rank_fusion
    participant Rerank as FlashRank reranker (optional)
    participant KG as kg_query_analysis
    participant PG as Postgres

    Agent->>HTTP: POST /mcp { tools/call brain_recall query="X" }
    HTTP->>MW: Bearer + Origin + Profile resolve
    MW->>MW: REQUEST_PROJECT_ID / AGENT_ID / SCOPE / PROFILE set
    MW->>MCP: forward
    MCP->>TF: lookup tool in profile allow-list
    TF-->>MCP: allowed
    MCP->>Tool: invoke brain_recall(query, ...)
    Tool->>Ctx: get or build MemoryStore(project_id, agent_id)
    Ctx-->>Tool: store proxy
    Tool->>Svc: brain_recall(store, project_id, agent_id, query=...)
    Svc->>Store: recall(query, filters)
    Store->>KG: analyze_query(query) — extract entity mentions
    KG->>PG: batch_resolve_entities
    PG-->>KG: entity_ids
    KG-->>Store: QueryAnalysis
    Store->>Retr: search(query, ...)
    par BM25 path
        Retr->>BM: score(tokens, corpus)
        BM->>PG: SELECT ... WHERE tsvector @@ plainto_tsquery
        PG-->>BM: rows
        BM-->>Retr: BM25 ranked
    and Vector path
        Retr->>Vec: embedding <=> query_embedding<br/>(HNSW: m=16, ef=200, cosine)
        Vec->>PG: SELECT ... ORDER BY <=> LIMIT pool
        PG-->>Vec: rows
        Vec-->>Retr: vector ranked
    end
    Retr->>Fus: RRF(bm25_list, vector_list, k=60,<br/>c_bm25/c_vector from hybrid_fusion profile)
    Fus-->>Retr: fused ranks
    opt rerank enabled (FlashRank)
        Retr->>Rerank: rerank top-20 candidates
        Rerank-->>Retr: reranked top_k
    end
    Retr-->>Store: ScoredMemory[]
    Store->>Store: apply lazy decay + filters<br/>(stale, low confidence, contradicted, superseded)
    Store->>Store: inject_memories — token budget + RAG safety
    Store-->>Svc: RecallResult (memories, hive_memory_count,<br/>recall_diagnostics, quality_warning)
    Svc-->>Tool: serializable dict
    Tool-->>MCP: json.dumps(result)
    MCP-->>HTTP: tools/call response
    HTTP-->>Agent: ranked memories + diagnostics
```

References: [docs/guides/auto-recall.md](../guides/auto-recall.md), [docs/guides/decay.md](../guides/decay.md), [docs/guides/knowledge-graph.md](../guides/knowledge-graph.md).

---

## 8. Sequence — `brain_remember` write path

The save pipeline: safety → write policy → conflict resolution → persistence → propagation → consolidation. From [services/memory_service.py::brain_remember](../../src/tapps_brain/services/memory_service.py), [store.py::MemoryStore.save](../../src/tapps_brain/store.py), and [_save_pipeline.py](../../src/tapps_brain/_save_pipeline.py).

```mermaid
sequenceDiagram
    autonumber
    participant Agent as Coding Agent
    participant Tool as tools_brain.brain_remember
    participant Svc as services.brain_remember
    participant Store as MemoryStore.save
    participant Safety as safety.check_content_safety
    participant Policy as WritePolicy<br/>(Deterministic or LLM)
    participant Conflict as _save_conflict<br/>(plan_conflicts)
    participant RL as rate_limiter
    participant Bloom as BloomFilter
    participant PG as PostgresPrivateBackend
    participant Integ as integrity.compute_integrity_hash_v2
    participant Audit as audit_log
    participant Prop as _save_propagation
    participant Hive as PostgresHiveBackend
    participant Auto as auto_consolidation.check_consolidation_on_save

    Agent->>Tool: brain_remember(fact="...", tier="procedural", share_with="...")
    Tool->>Svc: brain_remember(store, ...)
    Svc->>Store: save(key, value, tier, agent_scope, memory_group, ...)
    Store->>Safety: check_content_safety(value)
    alt content_blocked
        Safety-->>Store: blocked
        Store-->>Svc: { ok: false, error: content_blocked }
    else allowed (maybe sanitized)
        Safety-->>Store: ok
        Store->>RL: check sliding-window quota
        alt over limit
            RL-->>Store: warn-only (logged)
        end
        Store->>Bloom: maybe_seen(key, value_hash)
        alt definitely-not-seen fast path
            Bloom-->>Store: skip similarity check
        else maybe-seen
            Bloom-->>Store: do full conflict check
            Store->>Conflict: plan_conflicts(new, existing)
            Conflict-->>Store: ConflictPlan (supersede / merge / dedup / ADD)
        end
        Store->>Policy: decide(new, conflict_plan)
        Policy-->>Store: ADD / UPDATE / DELETE / NOOP
        opt ADD or UPDATE
            Store->>Integ: hmac_v2(key, value, tier, source)
            Integ-->>Store: integrity_hash
            Store->>PG: INSERT/UPDATE private_memories<br/>(RLS: project_id, agent_id)
            PG-->>Store: row written
            Store->>Audit: append save event (Postgres audit_log)
        end
        opt agent_scope != private
            Store->>Prop: propagate_group_save(entry, agent_scope, share_with)
            Prop->>Hive: INSERT hive_memories<br/>LISTEN/NOTIFY change feed
            Hive-->>Prop: ok
        end
        Store->>Auto: check_consolidation_on_save(new_entry)
        Auto->>Store: maybe merge similar entries<br/>(Jaccard + TF-cosine + optional embedding)
        Auto-->>Store: ConsolidationResult (audit row written)
        Store-->>Svc: { ok: true, key, integrity_hash }
    end
    Svc-->>Tool: result
    Tool-->>Agent: response
```

References: [docs/engineering/call-flows.md](call-flows.md), [docs/guides/save-conflict-nli-offline.md](../guides/save-conflict-nli-offline.md), [docs/guides/write-path-tradeoff.md](../guides/write-path-tradeoff.md), [src/tapps_brain/integrity.py](../../src/tapps_brain/integrity.py).

---

## 9. Sequence — `brain_record_event` (atomic KG write)

The single-transaction path: one event + memory + entities + edges + evidence committed atomically. From [experience.py::ExperienceEventRecorder.record](../../src/tapps_brain/experience.py).

```mermaid
sequenceDiagram
    autonumber
    participant Agent
    participant Tool as tools_kg.brain_record_event
    participant Svc as services.kg_service.record_event
    participant Rec as ExperienceEventRecorder
    participant CM as PostgresConnectionManager
    participant Conn as psycopg Connection (TX)
    participant Memo as private_memories table
    participant Ent as kg_entities table
    participant Edge as kg_edges table
    participant Ev as evidence table
    participant Evt as experience_events table

    Agent->>Tool: brain_record_event(event_type, payload, memory, entities, edges)
    Tool->>Svc: record_event(cm, project_id, brain_id, ExperienceEvent)
    Svc->>Rec: record(event)
    Rec->>CM: project_context(project_id)
    CM->>Conn: BEGIN transaction (RLS scope set)
    Note over Conn: All writes below share<br/>one psycopg transaction
    Rec->>Evt: INSERT experience_events
    opt memory provided
        Rec->>Memo: INSERT private_memories
    end
    opt entities provided
        Rec->>Ent: UPSERT kg_entities (canonical_name UPSERT)
        Ent-->>Rec: entity_ids
    end
    opt edges provided
        Rec->>Edge: UPSERT kg_edges (predicate, src, dst, confidence)
        Note over Edge: evidence_required=True by default;<br/>edges without evidence cap confidence at 0.4
    end
    opt evidence provided
        Rec->>Ev: INSERT evidence rows<br/>(quote, source_uri, source_type)
    end
    alt all succeeded
        Conn->>Conn: COMMIT
        Rec-->>Svc: ExperienceResult(event_id, entity_ids, edge_ids)
    else any error
        Conn->>Conn: ROLLBACK
        Note over Conn: Including the event row —<br/>no partial writes possible
        Rec-->>Svc: raise IntegrityError
    end
    Svc-->>Tool: result
    Tool-->>Agent: ok
```

References: [docs/engineering/experience-events.md](experience-events.md), [docs/planning/adr/ADR-012-evidence-required-edges.md](../planning/adr/ADR-012-evidence-required-edges.md), [docs/planning/adr/ADR-011-kg-schema-postgres.md](../planning/adr/ADR-011-kg-schema-postgres.md).

---

## 10. Sequence — Hive propagation

How a `share_with="domain"` write fans out to other agents. From [_save_propagation.py](../../src/tapps_brain/_save_propagation.py) and [postgres_hive.py](../../src/tapps_brain/postgres_hive.py).

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent A (publisher)
    participant Store as MemoryStore (A)
    participant Prop as _save_propagation
    participant Scope as agent_scope.normalize_agent_scope
    participant Hive as PostgresHiveBackend
    participant PG as Postgres (hive_memories)
    participant Sub as Agents B, C, … (subscribers)
    participant ASub as Subscriber AgentBrain (recall)

    A->>Store: save(key, value, agent_scope="domain", memory_group="frontend-guild")
    Store->>Scope: normalize("domain", group="frontend-guild")
    Scope-->>Store: ("hive", group_namespace="frontend-guild")
    Store->>Prop: propagate_group_save(entry, hive_backend)
    Prop->>Hive: insert_memory(entry, namespace="frontend-guild")
    Hive->>PG: INSERT hive_memories (project_id, namespace, key, value,<br/>embedding, tsvector, agent_id, source_agent)
    PG-->>Hive: ok
    Hive->>PG: NOTIFY hive_change, payload={namespace, key, op:'insert'}
    PG--)Sub: LISTEN hive_change channel
    Note over Sub: Subscribers wake up<br/>(if hive_watch active)
    Sub->>ASub: refresh local cache (optional)

    Note over Store,Hive: Async path: Agent B<br/>does a normal recall later
    ASub->>Store: recall("frontend topic")
    Store->>Hive: search(query, namespace="frontend-guild")
    Hive->>PG: SELECT hive_memories WHERE tsvector @@ ... AND namespace=...<br/>+ pgvector ANN
    PG-->>Hive: rows
    Hive-->>Store: hive results
    Store->>Store: merge local + hive (weight default 0.8)
    Store-->>ASub: RecallResult (hive_memory_count > 0)
```

References: [docs/guides/hive.md](../guides/hive.md), [docs/guides/hive-deployment.md](../guides/hive-deployment.md), [docs/guides/hive-vs-federation.md](../guides/hive-vs-federation.md), [docs/planning/epics/EPIC-056.md](../planning/epics/EPIC-056.md).

---

## 11. Dependency overview

Auto-generated import graph (truncated to 50 of 138 modules; see [docs/architecture.html](../architecture.html) for the full interactive viewer).

```mermaid
graph LR
    subgraph public["Public surface"]
      agent_brain[agent_brain.py]:::business
      client[client.py]:::presentation
      aio[aio.py]:::business
    end

    subgraph transport["Transport layer"]
      http_adapter[http_adapter.py]:::presentation
      http_mw[http/middleware.py]:::infra
      http_auth[http/auth.py]:::infra
      mcp_server[mcp_server/server.py]:::presentation
      mcp_tools[mcp_server/tools_*]:::infra
    end

    subgraph svc["Service layer (pure)"]
      memory_service[services/memory_service.py]:::business
      kg_service[services/kg_service.py]:::business
      hive_service[services/hive_service.py]:::business
      feedback_service[services/feedback_service.py]:::business
      flywheel_service[services/flywheel_service.py]:::business
      maintenance_service[services/maintenance_service.py]:::business
    end

    subgraph core["Memory core"]
      store[store.py - MemoryStore]:::business
      retrieval[retrieval.py - MemoryRetriever]:::business
      bm25[bm25.py]:::business
      fusion[fusion.py - RRF]:::business
      decay[decay.py]:::business
      consolidation[consolidation.py]:::business
      auto_consolidation[auto_consolidation.py]:::business
      safety[safety.py]:::business
      write_policy[write_policy.py]:::business
      contradictions[contradictions.py]:::business
      reinforcement[reinforcement.py]:::business
      promotion[promotion.py]:::business
      injection[injection.py]:::business
      seeding[seeding.py]:::business
      integrity[integrity.py]:::business
      rate_limiter[rate_limiter.py]:::business
      bloom[bloom.py]:::business
    end

    subgraph kg["Knowledge Graph"]
      experience[experience.py]:::business
      kg_query[kg_query_analysis.py]:::business
      relations[relations.py]:::business
    end

    subgraph backends_b["Backends (Postgres)"]
      backends[backends.py - factories]:::infra
      pg_private[postgres_private.py]:::data
      async_pg_private[async_postgres_private.py]:::data
      pg_hive[postgres_hive.py]:::data
      pg_federation[postgres_federation.py]:::data
      pg_kg[postgres_kg.py]:::data
      async_pg_kg[async_postgres_kg.py]:::data
      pg_conn[postgres_connection.py]:::data
      migrations[postgres_migrations.py]:::data
    end

    subgraph obs["Observability + quality"]
      otel_tracer[otel_tracer.py]:::infra
      otel_exporter[otel_exporter.py]:::infra
      metrics[metrics.py]:::infra
      diagnostics[diagnostics.py]:::business
      feedback[feedback.py]:::business
      flywheel[flywheel.py]:::business
      health_check[health_check.py]:::business
      recall_quality_buffer[recall_quality_buffer.py]:::infra
    end

    subgraph err["Errors + protocols"]
      protocols[_protocols.py]:::infra
      errors[errors.py]:::infra
      exceptions[exceptions.py]:::infra
    end

    agent_brain --> store
    agent_brain --> backends
    client --> errors
    aio --> store
    aio --> async_pg_private

    http_adapter --> mcp_server
    http_adapter --> memory_service
    http_adapter --> http_mw
    http_adapter --> http_auth
    mcp_server --> mcp_tools
    mcp_tools --> memory_service
    mcp_tools --> kg_service
    mcp_tools --> hive_service
    mcp_tools --> feedback_service

    memory_service --> store
    kg_service --> experience
    kg_service --> pg_kg
    hive_service --> pg_hive

    store --> retrieval
    store --> safety
    store --> write_policy
    store --> consolidation
    store --> auto_consolidation
    store --> contradictions
    store --> reinforcement
    store --> promotion
    store --> injection
    store --> seeding
    store --> integrity
    store --> rate_limiter
    store --> bloom
    store --> decay
    store --> pg_private

    retrieval --> bm25
    retrieval --> fusion

    experience --> pg_kg
    experience --> pg_private
    kg_query --> pg_kg

    backends --> pg_private
    backends --> pg_hive
    backends --> pg_federation
    backends --> pg_kg
    backends --> pg_conn

    store --> otel_tracer
    store --> metrics
    diagnostics --> feedback
    flywheel --> feedback
    health_check --> store
    health_check --> pg_hive

    memory_service --> errors
    kg_service --> errors
    errors --> exceptions

    classDef presentation fill:#F5A9D0,stroke:#333,color:#000
    classDef business fill:#14B8A6,stroke:#333,color:#fff
    classDef data fill:#9333EA,stroke:#333,color:#fff
    classDef infra fill:#6B7280,stroke:#333,color:#fff
```

---

## 12. Hexagonal / Ports-and-Adapters layering

```mermaid
flowchart TB
    subgraph adapters_in["Inbound adapters"]
      A1[HTTP /v1/* REST]:::presentation
      A2[MCP /mcp tools]:::presentation
      A3[Operator MCP :8090]:::presentation
      A4[CLI - tapps-brain]:::presentation
      A5[AgentBrain Python facade]:::presentation
      A6[TappsBrainClient SDK]:::presentation
    end

    subgraph core_h["Core domain - pure"]
      D1[services.* - business operations]:::business
      D2[MemoryStore - state + invariants]:::business
      D3[Retrieval pipeline - BM25 + vec + RRF]:::business
      D4[ExperienceEventRecorder - atomic KG]:::business
      D5[Consolidation + Decay + Promotion]:::business
      D6[Safety + WritePolicy]:::business
    end

    subgraph ports["Ports - _protocols.py"]
      P1[PrivateBackend]:::infra
      P2[HiveBackend]:::infra
      P3[FederationBackend]:::infra
      P4[KnowledgeGraphBackend]:::infra
      P5[AgentRegistryBackend]:::infra
      P6[WritePolicy]:::infra
      P7[Reranker]:::infra
      P8[EmbeddingProvider]:::infra
      P9[LookupEngine]:::infra
      P10[LLMJudge]:::infra
    end

    subgraph adapters_out["Outbound adapters"]
      O1[PostgresPrivateBackend]:::data
      O2[AsyncPostgresPrivateBackend]:::data
      O3[PostgresHiveBackend]:::data
      O4[PostgresFederationBackend]:::data
      O5[PostgresKnowledgeGraphStore]:::data
      O6[AsyncPostgresKnowledgeGraphStore]:::data
      O7[FileAgentRegistryBackend]:::data
      O8[PostgresAgentRegistry]:::data
      O9[FlashRankReranker - optional]:::infra
      O10[SentenceTransformerProvider - optional]:::infra
      O11[Context7 docs lookup - optional]:::infra
      O12[AnthropicJudge / OpenAIJudge - optional]:::infra
    end

    A1 --> D1
    A2 --> D1
    A3 --> D1
    A4 --> D1
    A5 --> D1
    A6 --> D1

    D1 --> D2
    D2 --> D3
    D2 --> D5
    D2 --> D6
    D1 --> D4

    D2 --> P1
    D2 --> P2
    D2 --> P3
    D4 --> P4
    D1 --> P5
    D2 --> P6
    D3 --> P7
    D3 --> P8
    D6 --> P9
    D5 --> P10

    P1 --> O1
    P1 --> O2
    P2 --> O3
    P3 --> O4
    P4 --> O5
    P4 --> O6
    P5 --> O7
    P5 --> O8
    P7 --> O9
    P8 --> O10
    P9 --> O11
    P10 --> O12

    classDef presentation fill:#F5A9D0,stroke:#333,color:#000
    classDef business fill:#14B8A6,stroke:#333,color:#fff
    classDef data fill:#9333EA,stroke:#333,color:#fff
    classDef infra fill:#6B7280,stroke:#333,color:#fff
```

This is the architectural invariant tapps-brain enforces. Core never imports from `tapps_brain.http.*` or `tapps_brain.postgres_*` directly — it depends only on the protocols in [`_protocols.py`](../../src/tapps_brain/_protocols.py). Backends are wired by the factories in [`backends.py`](../../src/tapps_brain/backends.py) based on DSN inspection.

---

## See also

- [docs/architecture.html](../architecture.html) — interactive viewer (Mermaid + pan/zoom + ToC)
- [docs/engineering/architecture-report.html](architecture-report.html) — printable HTML report with SVG flows
- [docs/engineering/system-architecture.md](system-architecture.md) — narrative architecture doc
- [docs/engineering/call-flows.md](call-flows.md) — text walkthrough of remember/recall/hive flows
- [docs/engineering/data-stores-and-schema.md](data-stores-and-schema.md) — Postgres DDL + indexes + migrations
- [docs/engineering/threat-model.md](threat-model.md) — STRIDE per public surface
- [docs/api-reference.md](../api-reference.md) — full API reference (autogenerated)
