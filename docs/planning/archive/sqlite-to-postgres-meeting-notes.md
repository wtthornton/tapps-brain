# SQLite to Postgres - Meeting Notes

Status: active discussion  
Last updated: 2026-03-31  
Owner: team discussion log

## Meeting objective

Capture migration discussion from SQLite to PostgreSQL, including rationale, trade-offs, ideas, and agreed recommendations.

## Ideas (parking lot)

- Keep SQLite as default for local/offline workflows and introduce Postgres as an optional backend profile.
- Define a storage abstraction boundary so retrieval, decay, consolidation, and feedback logic remain backend-agnostic.
- Start with a dual-write or shadow-read PoC for high-confidence validation before full cutover.
- Preserve current deterministic behavior and confidence/decay semantics across backends.
- Add export/import and schema compatibility checks to reduce migration risk.

## What we have discussed so far

- Current repository is heavily SQLite-centered (schema migrations, sqlite-vec, SQLCipher path).
- No first-party Postgres migration plan or PoC appears in core `src/` or primary docs yet.
- ~~`mem0-review/`~~ (removed from repo): was reference/research content only, not tapps-brain runtime.
- Migration planning should treat performance, portability, operational complexity, and reliability as first-class constraints.

## Key questions for next discussion

- Scope: optional Postgres backend, or full replacement target?
- Compatibility: should SQLite remain the default long-term for single-user local usage?
- Feature parity: which capabilities must be identical on day one (FTS behavior, vector search behavior, migrations, encryption story)?
- Operations: who runs and maintains Postgres in local/dev/prod environments?
- Rollback: what is the fallback plan if Postgres rollout regresses recall quality or latency?

## Recommendations at end of meeting

- Decision 1: Treat Postgres as an additive path first, not a breaking replacement.
- Decision 2: Create a formal design doc before coding (backend abstraction, migration strategy, test matrix).
- Decision 3: Build a constrained PoC that validates read/write parity on a representative fixture.
- Decision 4: Define explicit success metrics (latency, recall relevance parity, failure recovery behavior).

## Action items

- [ ] Draft architecture proposal for backend abstraction boundaries.
- [ ] Define migration phases (PoC -> beta optional backend -> production readiness).
- [ ] Create parity test plan across SQLite and Postgres backends.
- [ ] Identify required indexes and query paths for top recall/search workloads.
- [ ] Document deployment expectations for local, CI, and production-like environments.

## Change log

- 2026-03-31: Initial notes file created from discussion.
