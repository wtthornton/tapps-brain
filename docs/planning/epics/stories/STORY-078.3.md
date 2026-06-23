# Story 78.3 — Embedding model singleton: stop reload storm

<!-- docsmcp:start:user-story -->
> **As a** platform operator, **I want** the embedding model loaded once per process, **so that** concurrent MCP recall/save calls do not block HTTP health probes.
<!-- docsmcp:end:user-story -->

**Points:** 5 | **Epic:** EPIC-078 | **Priority:** P0

## What

Ensure `BAAI/bge-small-en-v1.5` (SentenceTransformer) is loaded at most once per `tapps-brain-http` process and never re-loaded on every MCP tool invocation.

## Where

- `src/tapps_brain/embeddings.py` — provider lifecycle
- `src/tapps_brain/http_adapter.py` — lifespan warmup hook (optional)
- Container logs — `Loading SentenceTransformer model from BAAI/bge-small-en-v1.5`

## Why

2026-06-22 logs show the model loading **once per CallToolRequest** — CPU/IO heavy, blocks threads, contributes to `/healthz` timeout and unhealthy container state.

## Tasks

- [ ] Audit embedding provider construction path; enforce process-wide singleton with threading lock
- [ ] Add structured log `embedding_model_loaded` once per process (not per request)
- [ ] Optional: eager-load in HttpAdapter lifespan when `TAPPS_BRAIN_EMBEDDING_REQUIRED=1`
- [ ] Integration test: 10 concurrent MCP recall calls → one model load log line

## Acceptance Criteria

- [ ] SentenceTransformer weights load **once** per process under 10 concurrent embedding requests (log assertion or counter)
- [ ] `/healthz` returns 200 within 5s after container start when embedding required
- [ ] No regression in embedding recall accuracy (existing pgvector tests pass)

## Test Cases

1. Sequential 5 embed calls → single load
2. Parallel 10 embed calls → single load
3. Process restart → load again (expected)
