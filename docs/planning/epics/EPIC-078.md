# Epic 78: Brain Visual Dashboard — Operational Recovery

<!-- docsmcp:start:metadata -->
**Status:** Proposed
**Priority:** P0 — Blocker
**Estimated LOE:** ~3 weeks (1 developer)
**Dependencies:** EPIC-065 (partial), EPIC-067 (compose wiring), EPIC-068 (multi-page UI shell)
**Linear epic:** [TAP-4297](https://linear.app/tappscodingagents/issue/TAP-4297/epic-078-brain-visual-dashboard-operational-recovery)
<!-- docsmcp:end:metadata -->

---

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

The brain-visual dashboard at `http://localhost:8088/` is the primary operator surface for a Docker-deployed tapps-brain stack, but it is **fully non-functional** in the live deployment: static HTML/CSS/JS loads while every data panel remains empty because `GET /snapshot` returns **504 Gateway Timeout** and `tapps-brain-http` is **unhealthy** (`/healthz` times out).

Investigation on **2026-06-22** confirmed a multi-layer failure chain spanning backend event-loop blocking, O(n) snapshot builds, nginx proxy timeouts, missing smoke coverage for the `:8088` path, and incomplete EPIC-065 live-monitoring panels. EPIC-067 is marked Complete but `/snapshot` acceptance criteria were never met on the running stack. EPIC-065 stories **65.3–65.7** remain open.

This epic restores end-to-end operability and hardens the dashboard/API contract so operators can trust the LIVE badge.
<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:goal -->
## Goal

Restore a working live dashboard at `:8088` with `GET /snapshot` completing within SLO (**<2s p95 warm**, **<10s cold**), `tapps-brain-http` healthy, actionable degraded-mode UX when upstream fails, completion of EPIC-065 remaining live panels, and automated smoke/regression gates.
<!-- docsmcp:end:goal -->

<!-- docsmcp:start:motivation -->
## Motivation

Operators following `docker/README.md` open `:8088` expecting a control room for memory health, Hive topology, and retrieval mode. Instead they see **OFFLINE → ERROR** with an empty-state message. The UI shell (six hash-routed pages, side-nav, help drawer) is complete (EPIC-068) but useless without live JSON.

Without this epic, the Docker stack appears broken on first use — undermining trust in tapps-brain as production infrastructure.
<!-- docsmcp:end:motivation -->

---

## Investigation findings (2026-06-22)

### Architecture

```
Browser :8088 (tapps-visual / nginx)
  ├── Static: index.html, brain-visual-router.js, brain-visual-help.js  → HTTP 200
  └── Proxy:  GET /snapshot → tapps-brain-http:8080/snapshot
              (Bearer + X-Project-Id injected by visual-entrypoint.sh)

tapps-brain-http :8080 (FastAPI HttpAdapter)
  ├── GET /snapshot  → build_visual_snapshot(store)  [sync, 15s TTL cache]
  ├── GET /healthz   → DB + schema probe
  └── GET /ready     → migration readiness

build_visual_snapshot (visual_snapshot.py)
  → store.health(), store.list_all(), diagnostics, hive, agent registry, scorecard
```

### Verified failures

| Probe | Result | Notes |
|-------|--------|-------|
| `GET :8088/` | **200** | ~193 KB HTML; meta `tapps-snapshot-url=/snapshot` |
| `GET :8088/snapshot` | **504** | nginx `proxy_read_timeout 10s`; upstream never responds in time |
| `GET :8080/healthz` | **timeout** | No response in 5–15s from host or `docker exec` |
| `tapps-brain-http` health | **unhealthy** | Up 6 days, healthcheck failing |
| `tapps-visual` logs | upstream timed out | `172.21.0.5:8080/snapshot` |
| `tapps-brain-http` logs | SentenceTransformer reload storm | `BAAI/bge-small-en-v1.5` loaded per MCP `CallToolRequest` |

### UI behavior without snapshot

- Poll interval default **30s**; **3 consecutive errors** → badge `ERROR · HTTP 504`
- Empty state shown: *"The live /snapshot endpoint is not responding"*
- All six pages (`#overview`, `#health`, `#memory`, `#retrieval`, `#agents`, `#integrity`) render shell only — no KPI strip, scorecard, charts, topology SVG, or export
- Static assets (`brain-visual-router.js`, `brain-visual-help.js`, SVG logo) load correctly

### API / ops gaps

- `scripts/brain_smoke_live.sh` tests `/healthz`, `/ready`, `/v1/experience` — **does not test `/snapshot`**
- No `make brain-visual-smoke-live` target for `:8088`
- Snapshot handler runs **sync** `build_visual_snapshot()` inside **async** route without `asyncio.to_thread`
- `build_visual_snapshot()` calls `store.list_all()` — **O(n)** on every cache miss
- nginx `proxy_read_timeout 10s` may be shorter than cold snapshot build on large stores

---

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] `GET http://localhost:8088/snapshot` returns HTTP 200 with valid VisualSnapshot JSON (`schema_version` 2) via nginx proxy within 10s cold / 2s warm
- [ ] `tapps-brain-http` Docker healthcheck (`/healthz`) passes consistently after stack boot
- [ ] Dashboard at `:8088` shows **LIVE** badge and populated panels (KPI strip, scorecard, tier chart) within one poll cycle
- [ ] `make brain-visual-smoke-live` passes against running stack (new target)
- [ ] `brain_smoke_live.sh` extended to assert `/snapshot` latency and schema
- [ ] Degraded-mode UI distinguishes **504 timeout** vs **401 auth** vs **503 no-store** with operator-action copy
- [ ] EPIC-065 stories **65.3–65.7** acceptance criteria met or explicitly descoped with rationale in EPIC-065
- [ ] Ops runbook documents OFFLINE triage path in `docs/guides/visual-snapshot.md` or `docs/guides/hive-deployment.md`

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:stories -->
## Stories

| Story | Title | Pts | Priority |
|-------|-------|-----|----------|
| [STORY-078.1](stories/STORY-078.1.md) | Unblock HTTP event loop — async snapshot build | 5 | P0 |
| [STORY-078.2](stories/STORY-078.2.md) | Optimize snapshot aggregates — eliminate full list_all scan | 8 | P0 |
| [STORY-078.3](stories/STORY-078.3.md) | Embedding model singleton — stop reload storm | 5 | P0 |
| [STORY-078.4](stories/STORY-078.4.md) | nginx visual proxy hardening + upstream error UX | 3 | P1 |
| [STORY-078.5](stories/STORY-078.5.md) | tapps-visual /healthz liveness endpoint | 2 | P2 |
| [STORY-078.6](stories/STORY-078.6.md) | Snapshot build SLO Prometheus metrics | 3 | P1 |
| [STORY-078.7](stories/STORY-078.7.md) | UI degraded-mode + HTTP error classification | 5 | P1 |
| [STORY-078.8](stories/STORY-078.8.md) | UI poll single-flight + exponential backoff | 3 | P2 |
| [STORY-078.9](stories/STORY-078.9.md) | Complete EPIC-065.3 — purge stale/privacy-gated components | 3 | P1 |
| [STORY-078.10](stories/STORY-078.10.md) | Complete EPIC-065.4 — Hive namespace monitoring table | 8 | P1 |
| [STORY-078.11](stories/STORY-078.11.md) | Complete EPIC-065.5 — Agent registry live table | 5 | P1 |
| [STORY-078.12](stories/STORY-078.12.md) | Complete EPIC-065.6 — Memory velocity panel wiring | 5 | P1 |
| [STORY-078.13](stories/STORY-078.13.md) | Complete EPIC-065.7 — Retrieval live metrics panel | 5 | P1 |
| [STORY-078.14](stories/STORY-078.14.md) | brain-visual-smoke-live + CI gate | 5 | P0 |
| [STORY-078.15](stories/STORY-078.15.md) | Dashboard OFFLINE ops runbook | 2 | P2 |

**Total:** 67 points

<!-- docsmcp:end:stories -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Snapshot route: `src/tapps_brain/http_adapter.py:1607-1641` — sync `build_visual_snapshot()` under `cfg.snapshot_lock`, 15s TTL
- Snapshot builder: `src/tapps_brain/visual_snapshot.py:879-1006` — `list_all()`, hive agent registry, diagnostics history (100 rows), feedback (200 rows)
- nginx proxy: `docker/nginx-visual.conf:13-24` — `proxy_read_timeout 10s`, Bearer + `X-Project-Id` placeholders
- UI polling: `examples/brain-visual/index.html:5259-5339` — 30s interval, STALE at 90s, ERROR at 3 failures
- Related epics: EPIC-065 (live dashboard, incomplete), EPIC-067 (compose, marked complete), EPIC-068 (UI shell, done)

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:non-goals -->
## Out of Scope

- WebGPU / 3D hero visual (brain-visual-implementation-plan Phase 3)
- React / bundler migration
- A/B snapshot diff (Phase B deferred)
- Replacing nginx with Caddy (documented alternative only)
- SSE/WebSocket live stream (Phase D)

<!-- docsmcp:end:non-goals -->

<!-- docsmcp:start:implementation-order -->
## Implementation Order

1. **078.1 + 078.3** — Restore `/healthz` and basic `/snapshot` responsiveness (unblock)
2. **078.2 + 078.4** — Performance + nginx timeout alignment
3. **078.14** — Smoke gate so regressions are caught immediately
4. **078.7 + 078.8** — Operator UX when upstream degraded
5. **078.9–078.13** — EPIC-065 panel completion
6. **078.5, 078.6, 078.15** — Observability + runbook polish

<!-- docsmcp:end:implementation-order -->

<!-- docsmcp:start:risk-assessment -->
## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Snapshot aggregate refactor changes fingerprint | Medium | Medium | Bump `identity_schema_version`; golden JSON tests |
| Embedding singleton fix affects MCP cold-start | Medium | Low | Benchmark before/after; lazy load unchanged |
| Large stores still exceed 10s until aggregates ship | High | High | Ship 078.1 first; raise nginx timeout temporarily in 078.4 |
| EPIC-065 panel scope creep | Medium | Medium | Each story inherits EPIC-065 AC verbatim |

<!-- docsmcp:end:risk-assessment -->

<!-- docsmcp:start:related-epics -->
## Related Epics

- **EPIC-065** — Live dashboard (65.1–65.2 done; 65.3–65.7 open)
- **EPIC-067** — Docker Hive stack completeness (marked Complete; live `/snapshot` still failing)
- **EPIC-068** — Multi-page hash router (Done)

<!-- docsmcp:end:related-epics -->
