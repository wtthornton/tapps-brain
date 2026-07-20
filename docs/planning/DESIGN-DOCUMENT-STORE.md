# DESIGN — Document plane for tapps-brain (documents beside vector RAG)

**Status:** Implemented (2026-07-20). Linear epic **TAP-4998**
(stories TAP-5002 through TAP-5005) in the tapps-brain project.
Shipped as migration 028 (`documents` + `document_chunks`),
`src/tapps_brain/documents.py`, `services/document_service.py`,
`mcp_server/tools_documents.py` (5 `document_*` tools), and the
`/v1/documents` HTTP routes.

**Origin:** Architecture review of AgentForge ADR-040 (HTTP fetch response
cache). The review asked: *why not enhance tapps-brain to support document/blob
storage along with vector RAG?* The answer split into two initiatives:

1. **HTTP fetch response cache** — stays in AgentForge (cache-aside at the
   fetching application; exact-key TTL byte cache is not a memory-product
   feature). See AgentForge `docs/adr/ADR-040-http-fetch-response-cache.md`,
   § Ownership.
2. **Document plane for knowledge content** — a legitimate tapps-brain product
   gap. This document designs it.

---

## Problem

tapps-brain today has a 4096-char `value` cap (`MAX_VALUE_LENGTH`) and only
*derivative* surfaces for larger content:

- `memory_ingest` / `MemoryStore.ingest_context` — rule-based fact extraction
  from free-form text; the source text is discarded.
- `session_chunks` (`SessionIndex`) — tsvector-searchable session summaries
  with TTL GC; not a general document store, no original-bytes retention.

There is no way for an agent to say “store this report / PDF text / meeting
transcript durably, let me (and other agents on the project) search it, and
keep the original retrievable.” Consumers work around this by truncating into
memory entries (RAG pollution), stashing files out-of-band (invisible to other
agents), or abusing memory as a blob cache (the ADR-040 incident: 400s on
oversize `/v1/remember`).

## What industry memory systems do (research, 2026-07)

- **Letta**: folders → file upload → automatic **chunk + embed** → semantic
  search via file tools / archival passages. Raw "Filesystem" byte access was
  **deprecated**. Documents are RAG sources with lifecycle status
  (`parsing` → `embedding` → `completed`).
- **Zep / Mem0**: extract facts / temporal graph edges from source content;
  the product surface is the distilled knowledge, not the bytes.
- **Common thread**: memory products treat documents as *ingestion sources
  for retrieval*, with metadata and processing state — none offer exact-key
  TTL blob caching. Blob-at-scale guidance is metadata-in-Postgres +
  object storage above ~1–10 MB per object; inline BYTEA is fine below that.

Design consequence: tapps-brain's document plane should be **ingest-first with
retained source**, not a generic blob bucket, and explicitly not a TTL cache.

## Proposal

Add a **`documents` plane** beside private memory, Hive, and Federation.

### Data model (Postgres, same tenancy as private memory)

```sql
CREATE TABLE documents (
  project_id    TEXT         NOT NULL,
  agent_id      TEXT         NOT NULL,          -- writer; reads are project-scoped
  doc_id        TEXT         NOT NULL,          -- ULID
  title         TEXT         NOT NULL,
  content_type  TEXT         NOT NULL DEFAULT 'text/plain',
  content       BYTEA        NOT NULL,          -- original bytes (text or binary)
  size_bytes    BIGINT       NOT NULL,
  sha256        TEXT         NOT NULL,
  tags          TEXT[]       NOT NULL DEFAULT '{}',
  index_status  TEXT         NOT NULL DEFAULT 'none',  -- none|pending|indexed|error
  retention     TEXT         NOT NULL DEFAULT 'project',  -- project|days:<n>
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ,                    -- NULL = keep until deleted
  PRIMARY KEY (project_id, doc_id)
);

CREATE TABLE document_chunks (
  project_id  TEXT    NOT NULL,
  doc_id      TEXT    NOT NULL,
  chunk_no    INT     NOT NULL,
  text        TEXT    NOT NULL,
  ts          TSVECTOR,                          -- lexical
  embedding   VECTOR(384),                       -- semantic (same model as memory)
  PRIMARY KEY (project_id, doc_id, chunk_no),
  FOREIGN KEY (project_id, doc_id)
    REFERENCES documents (project_id, doc_id) ON DELETE CASCADE
);
```

Row-level tenancy `(project_id, agent_id)` matches EPIC-053; RLS policies
mirror `private_memories`.

### API surface (HTTP + MCP)

| Operation | Surface |
|-----------|---------|
| `document_put` | `PUT /v1/documents` — title, content (text or base64), content_type, tags, `index: bool`, retention |
| `document_get` | `GET /v1/documents/{doc_id}` — metadata + content (content optional via `?meta_only=1`) |
| `document_search` | `POST /v1/documents:search` — hybrid tsvector + pgvector over `document_chunks`, RRF-fused like memory recall |
| `document_delete` | `DELETE /v1/documents/{doc_id}` |
| `document_list` | `GET /v1/documents` — metadata only, filterable by tag |

When `index=true`: synchronous deterministic chunking (same no-LLM stance as
consolidation) + embedding via the existing `embeddings.py` provider; chunks
land in `document_chunks`; `index_status` transitions `pending → indexed`.
When `index=false`: bytes stored, searchable by title/tags only.

Optionally, `document_put(extract=true)` also runs today's `ingest_context`
fact extraction so distilled facts land in memory with `source` pointing at
`doc:<doc_id>` — closing the loop between documents and memory without
duplicating content in `value`.

### Limits & governance

| Knob | Default | Note |
|------|---------|------|
| `documents.max_doc_bytes` | `2_097_152` (2 MiB) | Inline BYTEA comfort range; reject with `413 document_too_large` above |
| `documents.max_docs_per_project` | `500` | Profile-configurable, like `limits.max_entries` |
| `documents.max_chunks_per_doc` | `256` | Bounds embed cost |
| Retention GC | reuse `gc.py` sweep | `expires_at < now()` → archive/delete, metrics like `gc_archive` |

Object-storage backends are out of scope until a real >2 MiB need appears
(same deferral AgentForge ADR-024 made for run artifacts).

### Explicit non-goals

- **Not a TTL byte cache.** No exact-key HTTP response caching; AgentForge
  ADR-040 owns that. `expires_at` is retention policy, not cache semantics.
- **Not raising `MAX_VALUE_LENGTH`.** Memory `value` stays ≤ 4096; documents
  are a different table with different lifecycle (no decay, no consolidation,
  no confidence).
- **No LLM in the pipeline.** Chunking and extraction stay deterministic.
- **No cross-project reads in v1.** Federation of documents is a later
  question.

### Safety

`document_put` content passes the existing `safety.py` scan (prompt-injection
patterns) before chunks are eligible for retrieval injection; flagged documents
store with `index_status='error'` and a diagnostic, retrievable by ID but never
injected into RAG context.

## Consumers

- **AgentForge**: brain-side document ingest already lands via `memory_ingest`
  preference (AF 4.52.0 `POST /ingest`); a document plane gives it retained
  sources + searchable chunks instead of extraction-only.
- **Claude Code / Cursor agents**: store reports, ADR drafts, meeting notes,
  research dumps once; recall by hybrid search across sessions.
- **NLT pipelines**: scout research corpora, PE evaluation source material.

## Open questions

1. Should `document_search` results merge into `brain_recall` output (weighted
   like Hive results) or stay a separate tool? Leaning separate tool first.
2. Binary formats (PDF): store bytes + require caller-side text extraction, or
   add server-side extraction? Leaning caller-side in v1 (no new heavy deps).
3. Per-agent private documents vs project-shared default — proposal defaults to
   project-shared reads (matches the knowledge-sharing goal); revisit if a
   private-docs need appears.

## Delivery

Filed 2026-07-20 as epic **TAP-4998** with stories:

1. TAP-5002 — migrations: `documents` + `document_chunks` tables with RLS tenancy
2. TAP-5003 — document service + MCP tools + HTTP routes with size caps and safety gating
3. TAP-5004 — deterministic chunking, embedding, hybrid `document_search` with RRF
4. TAP-5005 — retention GC, limits enforcement, metrics, integration tests
