# Epic 65: Live Always-On Dashboard — Real-Time tapps-brain and Hive Monitoring

<!-- docsmcp:start:metadata -->
**Status:** Proposed
**Priority:** P1 - High
**Estimated LOE:** ~3-4 weeks (1 developer)
**Dependencies:** EPIC-060 (HttpAdapter foundation — done), EPIC-048 (visual_snapshot v2 schema — done), EPIC-030 (diagnostics composite score — done)

<!-- docsmcp:end:metadata -->

---

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

We are doing this so that operators running tapps-brain and the Hive in Docker can observe real system state — entry counts, agent activity, retrieval health, memory velocity, and hive namespace health — in a live browser dashboard without manually re-exporting snapshots. The existing dashboard is a static snapshot viewer; in a 24/7 Docker deployment it becomes stale the moment it loads. This epic wires the dashboard to the always-on HttpAdapter, adds a live /snapshot endpoint, removes or replaces every component that shows demo data or is gated behind a privacy tier that Docker operators never reach, and adds new panels that surface the information most critical to understanding a running Hive.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:goal -->
## Goal

Replace the static snapshot-file model with a live polling dashboard backed by a new GET /snapshot endpoint on the HttpAdapter. Purge all demo-data defaults, privacy-tier-gated sections that never render in production, and decorative pipeline diagrams with no real metrics. Add deep Hive monitoring (per-namespace counts, agent registry table, memory velocity), live retrieval health, and an agent activity panel — all auto-refreshing from the running container.

<!-- docsmcp:end:goal -->

<!-- docsmcp:start:motivation -->
## Motivation

tapps-brain 3.3.0 is now fully Docker-deployed and always-on. The visual dashboard was designed when the tool ran as a local library — operators would manually export a snapshot JSON and open the HTML file. That model does not fit a 24/7 Hive deployment: the snapshot is frozen the moment it is written, the demo brain-visual.json that ships in the repo (generated from a contributor's Windows machine at v2.0.3) shows 1176 foreign entries and 20 foreign agents on every fresh install, and entire dashboard sections (Tags, Memory Groups) are permanently hidden because they require a local privacy tier that Docker operators never configure. The HttpAdapter already runs in-process and already exposes /health, /ready, and /metrics. Adding /snapshot is a one-function addition. The dashboard fetch() call is a three-line change. The payoff is a monitoring surface that is always accurate, always current, and shows the right things for a server deployment.

<!-- docsmcp:end:motivation -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] GET /snapshot on HttpAdapter returns a valid VisualSnapshot JSON with HTTP 200 and correct Content-Type application/json
- [ ] Dashboard polls /snapshot at a configurable interval (default 30s) and updates all panels without page reload
- [ ] Connection status indicator shows live/stale/error with last-refresh timestamp
- [ ] Demo brain-visual.json is no longer loaded as the default data source — the live endpoint is tried first
- [ ] Tags section and Memory Groups section are removed from the default dashboard layout
- [ ] Static retrieval pipeline step-flow diagram is replaced by a live metrics panel showing query counts and mode
- [ ] Hive hub panel shows per-namespace entry counts in a table not just a comma-separated string
- [ ] Agent registry panel shows each registered agent with last-write timestamp and namespace
- [ ] Memory velocity panel shows write and recall counts over the last 1h and 24h windows
- [ ] All panels that previously required a snapshot file to be manually exported work automatically in Docker
- [ ] No demo or contributor data appears on first load — panels show real system state or a clear empty state
- [ ] Dashboard passes visual review against NLT Labs style guide (dark theme
- [ ] brand tokens
- [ ] no generic AI aesthetics)

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:stories -->
## Stories

### 65.1 -- GET /snapshot live endpoint on HttpAdapter

**Points:** 5

Add GET /snapshot to HttpAdapter that calls build_visual_snapshot() with a 15s TTL cache and returns VisualSnapshot JSON. Wire auth token gate. Add CORS header. Add to OpenAPI spec.

**Tasks:**
- [ ] Implement get /snapshot live endpoint on httpadapter
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** GET /snapshot live endpoint on HttpAdapter is implemented, tests pass, and documentation is updated.

---

### 65.2 -- Dashboard live polling mode

**Points:** 5

Replace static brain-visual.json fetch with setInterval poll against /snapshot. Add LIVE/STALE/ERROR connection badge and last-refreshed timestamp. Remove demo JSON as silent default.

**Tasks:**
- [ ] Implement dashboard live polling mode
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Dashboard live polling mode is implemented, tests pass, and documentation is updated.

---

### 65.3 -- Purge stale and privacy-gated components

**Points:** 3

Delete Tags section, Memory Groups section, static pipeline step-flow diagram, and scorecard-derive.js. Replace privacy footer with dynamic badge. Ship empty brain-visual.json stub.

**Tasks:**
- [ ] Implement purge stale and privacy-gated components
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Purge stale and privacy-gated components is implemented, tests pass, and documentation is updated.

---

### 65.4 -- Hive hub deep monitoring panel

**Points:** 8

Extend HiveHealthSummary with per-namespace entry counts and last_write_at. Render as a structured table in the dashboard replacing the prose string.

**Tasks:**
- [ ] Implement hive hub deep monitoring panel
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Hive hub deep monitoring panel is implemented, tests pass, and documentation is updated.

---

### 65.5 -- Agent registry live table

**Points:** 5

Add agent_registry list to VisualSnapshot from AgentRegistry.list_agents(). Render sortable table with last-write delta, silence highlighting, and empty state.

**Tasks:**
- [ ] Implement agent registry live table
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Agent registry live table is implemented, tests pass, and documentation is updated.

---

### 65.6 -- Memory velocity panel

**Points:** 5

Add MemoryVelocity (writes_1h, recalls_1h, writes_24h, recalls_24h) to VisualSnapshot via COUNT queries. Render 2x2 stat grid with delta arrows.

**Tasks:**
- [ ] Implement memory velocity panel
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Memory velocity panel is implemented, tests pass, and documentation is updated.

---

### 65.7 -- Retrieval pipeline live metrics panel

**Points:** 5

Add RetrievalMetrics from OTel in-process counters to VisualSnapshot. Replace static step-flow diagram with 5-tile metrics panel (queries, BM25 hits, vector hits, RRF fusions, avg latency).

**Tasks:**
- [ ] Implement retrieval pipeline live metrics panel
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** Retrieval pipeline live metrics panel is implemented, tests pass, and documentation is updated.

---

<!-- docsmcp:end:stories -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- HttpAdapter runs in a background thread — build_visual_snapshot() must be called with a store reference injected at adapter construction time; add optional store: MemoryStore | None parameter
- build_visual_snapshot() performs store.list_all() which is O(n) — add a TTL cache (default 15s) so rapid polls do not hammer SQLite
- GET /snapshot must be in the public route set (no auth required) or behind the same bearer token as /info — make this configurable
- Dashboard fetch() currently loads brain-visual.json from the same nginx origin; in Docker the HttpAdapter runs on port 8080 while nginx runs on port 80 — nginx proxy_pass /snapshot → 8080 is the standard wiring
- AgentRegistry is in postgres_hive.py — ensure it is accessible without a full hive store instantiation for the /snapshot collect path
- OTel counters for retrieval stages are in otel_tracer.py — verify they are accumulated in-process not just exported to OTLP so they can be read back for /snapshot
- Memory velocity windows (1h/24h) can be derived from created_at/last_accessed timestamps in Postgres — no new instrumentation needed
- NLT Labs style tokens are defined in the fingerprint-derived theme block — all new panels must consume these tokens not hardcoded hex values

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:non-goals -->
## Out of Scope / Future Considerations

- No WebSocket or SSE — polling is sufficient for a monitoring dashboard at 30s cadence
- No historical time-series storage — velocity windows are in-memory approximations only
- No authentication UI in the dashboard — bearer token is supplied via environment variable
- No mobile layout — dashboard targets 1280px+ operator workstations

<!-- docsmcp:end:non-goals -->

<!-- docsmcp:start:files-affected -->
## Files Affected

| File | Lines | Recent Commits | Public Symbols |
|------|-------|----------------|----------------|
| `src/tapps_brain/http_adapter.py` | 663 | 3 recent: 29c358b fix(lint): resolve 18 ruff errors acros... | 1 classes |
| `src/tapps_brain/visual_snapshot.py` | 808 | 5 recent: 0612078 feat(story-059.2): remove SQLite hive/f... | 8 classes, 5 functions |
| `src/tapps_brain/health_check.py` | 406 | 5 recent: 2e18811 chore(059.CLEAN): sweep stale SQLite re... | 4 classes, 2 functions |
| `src/tapps_brain/otel_tracer.py` | 535 | 5 recent: 9952f28 feat(story-032.7+032.8): add feedback a... | 8 functions |
| `examples/brain-visual/index.html` | 2892 | 5 recent: 8be6754 feat(story-064.5): add demo snapshot an... | - |
| `examples/brain-visual/brain-visual-help.js` | 1061 | 3 recent: 4cfb350 feat(story-064.4): add deep insight pan... | - |
| `examples/brain-visual/scorecard-derive.js` | 264 | 1 recent: 5440b59 feat(epic-044): GC metrics, consolidati... | - |
| `examples/brain-visual/brain-visual.json` | 180 | 2 recent: 5440b59 feat(epic-044): GC metrics, consolidati... | - |
| `docker/docker-compose.hive.yaml` | 48 | 3 recent: 63b2280 feat(docker): add tapps-visual nginx se... | - |
| `docker/Dockerfile.visual` | 3 | 1 recent: 63b2280 feat(docker): add tapps-visual nginx se... | - |

<!-- docsmcp:end:files-affected -->

<!-- docsmcp:start:related-epics -->
## Related Epics

- **EPIC-042.md** -- references `src/tapps_brain/health_check.py`
- **EPIC-047.md** -- references `src/tapps_brain/health_check.py`
- **EPIC-048.md** -- references `src/tapps_brain/visual_snapshot.py`
- **EPIC-052.md** -- references `src/tapps_brain/visual_snapshot.py`
- **EPIC-058.md** -- references `docker/docker-compose.hive.yaml`, `src/tapps_brain/health_check.py`
- **EPIC-064.md** -- references `examples/brain-visual/brain-visual-help.js`, `examples/brain-visual/index.html`, `src/tapps_brain/visual_snapshot.py`
- **EPIC-065.md** -- references `docker/Dockerfile.visual`, `docker/docker-compose.hive.yaml`, `examples/brain-visual/brain-visual-help.js`, `examples/brain-visual/brain-visual.json`, `examples/brain-visual/index.html`, `examples/brain-visual/scorecard-derive.js`, `src/tapps_brain/health_check.py`, `src/tapps_brain/http_adapter.py`, `src/tapps_brain/visual_snapshot.py`

<!-- docsmcp:end:related-epics -->

<!-- docsmcp:start:success-metrics -->
## Success Metrics

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| Zero foreign or demo entries on first load of a fresh Docker deployment | - | - | - |
| All panels refresh without page reload within 35s of a write to the store | - | - | - |
| /snapshot p99 response time under 200ms with TTL cache active | - | - | - |
| Hive hub shows correct per-namespace counts matching direct psql SELECT | - | - | - |
| Agent registry table lists all agents registered in AgentRegistry with correct last-write timestamps | - | - | - |

<!-- docsmcp:end:success-metrics -->

<!-- docsmcp:start:stakeholders -->
## Stakeholders

| Role | Person | Responsibility |
|------|--------|----------------|
| tapps-brain operators running Hive deployments | - | - |
| NLT Labs design review (style guide compliance) | - | - |
| OpenClaw integration team (agent registry visibility) | - | - |

<!-- docsmcp:end:stakeholders -->

<!-- docsmcp:start:implementation-order -->
## Implementation Order

1. Story 65.1: GET /snapshot live endpoint on HttpAdapter
2. Story 65.2: Dashboard live polling mode
3. Story 65.3: Purge stale and privacy-gated components
4. Story 65.4: Hive hub deep monitoring panel
5. Story 65.5: Agent registry live table
6. Story 65.6: Memory velocity panel
7. Story 65.7: Retrieval pipeline live metrics panel

<!-- docsmcp:end:implementation-order -->

<!-- docsmcp:start:risk-assessment -->
## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| build_visual_snapshot() is not thread-safe if called from the HttpAdapter background thread while the main thread writes — needs read lock or copy-on-read pattern | Medium | Medium | Warning: Mitigation required - no automated recommendation available |
| AgentRegistry table may not exist on fresh hive schemas pre-v3.3 — guard with try/except and return empty list | Medium | Medium | Warning: Mitigation required - no automated recommendation available |
| CORS policy between nginx:80 and HttpAdapter:8080 may be blocked by browser — nginx proxy_pass /api/ → 8080 is the mitigation | Medium | Medium | Warning: Mitigation required - no automated recommendation available |
| Memory velocity requires persistent counters across /snapshot calls — ephemeral in-process counters reset on container restart; document this limitation | Medium | Medium | Warning: Mitigation required - no automated recommendation available |

<!-- docsmcp:end:risk-assessment -->

<!-- docsmcp:start:performance-targets -->
## Performance Targets

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| Test coverage | baseline | >= 80% | pytest --cov |
| Acceptance criteria pass rate | 0% | 100% | CI pipeline |
| Quality gate score | N/A | >= 70/100 | tapps_quality_gate |
| Story completion rate | 0% | 100% | Sprint tracking |

<!-- docsmcp:end:performance-targets -->
