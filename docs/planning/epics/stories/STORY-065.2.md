# Story 65.2 -- Dashboard live polling mode

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain operator, **I want** the dashboard to automatically refresh from the live /snapshot endpoint every 30 seconds, **so that** I never see stale demo data and do not need to manually re-export a snapshot to see current system state

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the dashboard becomes a true monitoring surface rather than a static snapshot viewer. Once /snapshot is live (STORY-065.1), the dashboard should consume it automatically, show a visible connection status, and refresh on a timer — turning the nginx-served HTML into a live ops panel without any operator action beyond opening the browser.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Replace the current fetch(brain-visual.json) bootstrap sequence with a poll loop against the HttpAdapter /snapshot endpoint. On first load, attempt /snapshot; if unreachable fall back to brain-visual.json with a visible STALE banner. Add a configurable TAPPS_SNAPSHOT_URL (default: http://localhost:8080/snapshot) read from a meta tag injected by nginx at container build time. Add a connection status badge in the header: green LIVE with last-refreshed timestamp, amber STALE if last success > 90s ago, red ERROR if three consecutive failures. Expose a refresh interval control in the UI (15s / 30s / 60s / manual). Remove brain-visual.json as a silent default — if /snapshot is unreachable AND no file is loaded, show an explicit empty state with setup instructions.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `examples/brain-visual/index.html`
- `examples/brain-visual/brain-visual-help.js`
- `docker/Dockerfile.visual`
- `docker/docker-compose.hive.yaml`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Replace fetch(snapshotUrl) bootstrap with startLivePolling(url, intervalMs) function using setInterval (`examples/brain-visual/index.html`)
- [ ] Add connection status badge element to header HTML — three states: live (green), stale (amber), error (red) with last-refreshed text (`examples/brain-visual/index.html`)
- [ ] Add TAPPS_SNAPSHOT_URL meta tag injection to Dockerfile.visual nginx config or index.html template (`docker/Dockerfile.visual`)
- [ ] Add refresh interval selector control to header area (15s/30s/60s/manual radio group) (`examples/brain-visual/index.html`)
- [ ] Add explicit empty state panel when no data is loaded — instructions to check HttpAdapter is running (`examples/brain-visual/index.html`)
- [ ] Remove silent brain-visual.json default fallback — if /snapshot unreachable show STALE banner with last-known data, not silently show demo JSON (`examples/brain-visual/index.html`)
- [ ] Update nginx.conf in Dockerfile.visual to proxy /snapshot to http://tapps-brain-mcp:8080/snapshot so same-origin fetch works without CORS (`docker/Dockerfile.visual`)
- [ ] Add help entry for connection status badge in brain-visual-help.js (`examples/brain-visual/brain-visual-help.js`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] Dashboard fetches /snapshot on load and updates all panels without page reload
- [ ] LIVE badge shows with timestamp that increments every refresh cycle
- [ ] STALE badge appears if last successful fetch was more than 90 seconds ago
- [ ] ERROR badge appears after 3 consecutive fetch failures with the last error message
- [ ] Refresh interval selector changes poll cadence immediately without page reload
- [ ] If /snapshot is unreachable on first load an empty state is shown (no file-load or demo fallback)
- [ ] nginx proxy_pass routes /snapshot to HttpAdapter so fetch is same-origin from the browser perspective

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Dashboard live polling mode code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. Live path: /snapshot returns 200 → LIVE badge + panels populated
2. Stale path: mock /snapshot to return 200 once then go silent → STALE badge after 90s
3. Error path: /snapshot always 503 → ERROR badge after 3 failures
4. Interval change: change selector from 30s to 15s → next fetch within 15s
5. Empty state: /snapshot unreachable → empty state panel visible (no demo/file-load fallback exists)
6. Proxy: curl http://localhost:8088/snapshot from host → proxied response from HttpAdapter

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- setInterval with 30000ms default; clear and restart on interval change
- fetch() error handling: catch network errors
- non-200 responses
- and JSON parse failures separately for accurate badge state
- nginx proxy_pass requires resolver directive in nginx.conf for Docker internal DNS; use resolver 127.0.0.11 valid=30s
- **Superseded 2026-04-13:** the demo snapshot and Load-demo/Load-snapshot-file fallbacks were removed entirely; the dashboard is live-only against `/snapshot`

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-065.1 (GET /snapshot endpoint must exist)

<!-- docsmcp:end:dependencies -->

<!-- docsmcp:start:invest -->
## INVEST Checklist

- [ ] **I**ndependent -- Can be developed and delivered independently
- [ ] **N**egotiable -- Details can be refined during implementation
- [x] **V**aluable -- Delivers value to a user or the system
- [x] **E**stimable -- Team can estimate the effort
- [x] **S**mall -- Completable within one sprint/iteration
- [x] **T**estable -- Has clear criteria to verify completion

<!-- docsmcp:end:invest -->
