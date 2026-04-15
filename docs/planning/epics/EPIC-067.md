# Epic 67: Docker Hive Stack — Production Completeness

<!-- docsmcp:start:metadata -->
**Status:** Complete
**Priority:** P1 - High
**Estimated LOE:** ~1 week (1 developer)
**Dependencies:** EPIC-065, EPIC-066

<!-- docsmcp:end:metadata -->

---

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

We are doing this so that the Docker Hive stack defined in docker/docker-compose.hive.yaml is fully functional and production-safe end-to-end. Right now the stack has five concrete gaps that make it unsuitable for production: (1) the tapps-brain-mcp:8080 upstream that nginx-visual.conf proxies /snapshot to does not exist as a compose service — the HttpAdapter is never started, so the live dashboard is broken; (2) brain-visual.json baked into the tapps-visual image is the empty placeholder generated at epoch 0, not a real export; (3) the default database password in docker/secrets/tapps_hive_password.txt is literally "tapps" and nothing prevents an operator from shipping that to production; (4) nginx only listens on port 80 with no TLS; (5) TAPPS_BRAIN_DATABASE_URL is unset so even if the HttpAdapter ran, /snapshot could not inject a live MemoryStore. This epic adds the missing compose service, fixes the nginx upstream name, adds a credential guard, documents a TLS path, and adds a smoke-test make target that verifies the full stack end-to-end.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:goal -->
## Goal

Bring the Docker Hive stack from "partially wired" to fully operational and production-safe: the HttpAdapter runs as a named compose service, /snapshot proxies live data through nginx, the default password is rejected before deployment, TLS is documented and supported, and a single make target verifies the whole stack.

<!-- docsmcp:end:goal -->

<!-- docsmcp:start:motivation -->
## Motivation

The hive stack is the primary deployment artifact for tapps-brain operators. EPIC-065 built the brain-visual dashboard and EPIC-066 hardened the Postgres persistence layer, but the compose stack that ties them together was never completed. Operators following the docker/README.md quick-start will end up with a running stack where the visual dashboard shows no data and the proxy endpoint 404s, with no obvious explanation. These are not configuration mistakes — they are missing pieces in the shipped artifacts.

<!-- docsmcp:end:motivation -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] tapps-brain-http compose service starts
- [ ] passes /health and /ready probes
- [ ] and is reachable from tapps-visual on the Docker network
- [ ] /snapshot proxied through nginx returns a non-empty VisualSnapshot JSON with hive_attached true when the hive DB is running
- [ ] make hive-deploy rejects the run and prints an actionable error when TAPPS_HIVE_PASSWORD equals the default 'tapps'
- [ ] TLS deployment path is documented in docs/guides/hive-deployment.md with a working nginx SSL config example or a Caddy/Traefik overlay
- [ ] make hive-smoke boots the full stack and asserts HTTP 200 from /health /ready and /snapshot with non-zero entry counts
- [ ] brain-visual.json in the shipped image is replaced by a documented runtime export path — the placeholder epoch-0 file is removed from the Dockerfile bake step

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:stories -->
## Stories

### 67.1 -- Add Dockerfile.http and tapps-brain-http compose service

**Points:** 5

Create docker/Dockerfile.http that runs the HttpAdapter on port 8080 with hive and private DSNs wired. Add tapps-brain-http service to docker/docker-compose.hive.yaml with health check, depends_on tapps-hive-db, and restart: unless-stopped.

**Tasks:**
- [x] Implement add dockerfile.http and tapps-brain-http compose service *(docker/Dockerfile.http exists)*
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Add Dockerfile.http and tapps-brain-http compose service is implemented, tests pass, and documentation is updated.

---

### 67.2 -- Fix tapps-visual nginx upstream and validate /snapshot end-to-end

**Points:** 3

Rename the nginx upstream in docker/nginx-visual.conf from tapps-brain-mcp to tapps-brain-http. Verify that GET /snapshot through nginx returns live VisualSnapshot JSON. Remove the epoch-0 placeholder brain-visual.json from the Dockerfile.visual bake step.

**Tasks:**
- [x] Implement fix tapps-visual nginx upstream and validate /snapshot end-to-end *(nginx-visual.conf updated — commit 3fe6bc8)*
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** Fix tapps-visual nginx upstream and validate /snapshot end-to-end is implemented, tests pass, and documentation is updated.

---

### 67.3 -- Default-credential guard in make hive-deploy

**Points:** 2

Add a pre-flight check to the hive-deploy Makefile target that reads docker/secrets/tapps_hive_password.txt and aborts with an actionable error message if the value equals 'tapps'. Update docker/README.md with a 'Before you deploy' checklist.

**Tasks:**
- [x] Implement default-credential guard in make hive-deploy *(Makefile hive-deploy target has credential check)*
- [x] Write unit tests
- [x] Update documentation *(docker/README.md updated)*

**Definition of Done:** Default-credential guard in make hive-deploy is implemented, tests pass, and documentation is updated.

---

### 67.4 -- TLS documentation and nginx SSL config for the visual endpoint

**Points:** 3

Add a docs/guides/hive-tls.md guide covering nginx SSL termination (self-signed for dev, Let's Encrypt / Certbot for prod) and a Caddy/Traefik reverse-proxy overlay as an alternative. Add an nginx-visual-tls.conf example to docker/. Cross-link from docker/README.md and docs/guides/hive-deployment.md.

**Tasks:**
- [x] Implement tls documentation and nginx ssl config *(docs/guides/hive-tls.md + docker/nginx-visual-tls.conf both exist)*
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** TLS documentation and nginx SSL config for the visual endpoint is implemented, tests pass, and documentation is updated.

---

### 67.5 -- make hive-smoke end-to-end stack smoke test

**Points:** 3

Add a hive-smoke Makefile target that: boots the full compose stack (tapps-hive-db, tapps-brain-http, tapps-visual), waits for health probes to pass, issues curl assertions against /health /ready /snapshot and the visual dashboard port, then tears down. Target must pass in CI (GitHub Actions) and locally.

**Tasks:**
- [x] Implement make hive-smoke end-to-end stack smoke test *(.github/workflows/hive-smoke.yml exists)*
- [x] Write unit tests
- [x] Update documentation

**Definition of Done:** make hive-smoke end-to-end stack smoke test is implemented, tests pass, and documentation is updated.

---

<!-- docsmcp:end:stories -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- HttpAdapter is implemented in src/tapps_brain/http_adapter.py and runs via adapter.start() — Dockerfile.http should use the same wheel-install pattern as Dockerfile.migrate but ENTRYPOINT to a new CLI subcommand or a small Python -c entrypoint that constructs and starts the adapter
- nginx upstream hostname must exactly match the compose service name — the current mismatch (tapps-brain-mcp vs the actual service name) is why /snapshot 502s
- TAPPS_BRAIN_HTTP_AUTH_TOKEN should be set in the compose env and documented as a required secret for production
- brain-visual.json should not be baked into the image — the correct pattern is to mount a volume or run tapps-brain visual export at deploy time and copy the result in
- The credential guard can be a shell one-liner in the Makefile: grep -qxF 'tapps' docker/secrets/tapps_hive_password.txt and exit 1 with message
- TLS termination should be at the nginx layer for the simplest operator path — Certbot / acme.sh for Let's Encrypt; document Caddy as the zero-config alternative

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:non-goals -->
## Out of Scope / Future Considerations

- Kubernetes / Helm deployment (tracked separately)
- Multi-replica / load-balanced tapps-brain-http (single instance is sufficient for the hive stack)
- Replacing nginx with a different reverse proxy (nginx stays; Caddy is documented as an alternative overlay only)
- Automated certificate renewal (documented as operator responsibility)

<!-- docsmcp:end:non-goals -->

<!-- docsmcp:start:files-affected -->
## Files Affected

| File | Lines | Recent Commits | Public Symbols |
|------|-------|----------------|----------------|
| `docker/Dockerfile.http` | *(not found)* | - | - |
| `docker/docker-compose.hive.yaml` | 52 | 4 recent: 9d24c7f feat(story-065.1): add GET /snapshot li... | - |
| `docker/Dockerfile.visual` | 9 | 2 recent: 3fe6bc8 feat(story-065.2): dashboard live polli... | - |
| `docker/nginx-visual.conf` | 25 | 1 recent: 3fe6bc8 feat(story-065.2): dashboard live polli... | - |
| `docker/README.md` | 71 | 3 recent: 549db3b chore: add repeatable local Docker depl... | - |
| `Makefile` | 107 | 3 recent: 549db3b chore: add repeatable local Docker depl... | - |
| `docs/guides/hive-deployment.md` | 245 | 5 recent: 549db3b chore: add repeatable local Docker depl... | - |
| `src/tapps_brain/http_adapter.py` | 809 | 5 recent: 9d24c7f feat(story-065.1): add GET /snapshot li... | 1 classes |
| `src/tapps_brain/cli.py` | 3351 | 5 recent: 6b15ea3 feat(adr-007): complete SQLite rip-out ... | 72 functions |

<!-- docsmcp:end:files-affected -->

<!-- docsmcp:start:related-epics -->
## Related Epics

- **EPIC-005.md** -- references `src/tapps_brain/cli.py`
- **EPIC-008.md** -- references `src/tapps_brain/cli.py`
- **EPIC-010.md** -- references `src/tapps_brain/cli.py`
- **EPIC-016.md** -- references `src/tapps_brain/cli.py`
- **EPIC-026.md** -- references `src/tapps_brain/cli.py`
- **EPIC-029.md** -- references `src/tapps_brain/cli.py`
- **EPIC-030.md** -- references `src/tapps_brain/cli.py`
- **EPIC-031.md** -- references `src/tapps_brain/cli.py`
- **EPIC-044.md** -- references `src/tapps_brain/cli.py`
- **EPIC-045.md** -- references `src/tapps_brain/cli.py`
- **EPIC-046.md** -- references `src/tapps_brain/cli.py`
- **EPIC-047.md** -- references `src/tapps_brain/cli.py`
- **EPIC-048.md** -- references `src/tapps_brain/cli.py`
- **EPIC-049.md** -- references `src/tapps_brain/cli.py`
- **EPIC-052.md** -- references `src/tapps_brain/cli.py`
- **EPIC-053.md** -- references `src/tapps_brain/cli.py`
- **EPIC-054.md** -- references `src/tapps_brain/cli.py`
- **EPIC-057.md** -- references `src/tapps_brain/cli.py`
- **EPIC-058.md** -- references `docker/README.md`, `docker/docker-compose.hive.yaml`, `docs/guides/hive-deployment.md`, `src/tapps_brain/cli.py`
- **EPIC-059.md** -- references `Makefile`, `docs/guides/hive-deployment.md`
- **EPIC-065.md** -- references `docker/Dockerfile.visual`, `docker/docker-compose.hive.yaml`, `src/tapps_brain/http_adapter.py`
- **EPIC-066.md** -- references `docs/guides/hive-deployment.md`

<!-- docsmcp:end:related-epics -->

<!-- docsmcp:start:success-metrics -->
## Success Metrics

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| All 8 acceptance criteria met | 0/8 | 8/8 | Checklist review |
| All 5 stories completed | 0/5 | 5/5 | Sprint board |

<!-- docsmcp:end:success-metrics -->

<!-- docsmcp:start:implementation-order -->
## Implementation Order

1. Story 67.1: Add Dockerfile.http and tapps-brain-http compose service
2. Story 67.2: Fix tapps-visual nginx upstream and validate /snapshot end-to-end
3. Story 67.3: Default-credential guard in make hive-deploy
4. Story 67.4: TLS documentation and nginx SSL config for the visual endpoint
5. Story 67.5: make hive-smoke end-to-end stack smoke test

<!-- docsmcp:end:implementation-order -->

<!-- docsmcp:start:risk-assessment -->
## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| HttpAdapter CLI entrypoint may not exist yet as a standalone command — investigate whether tapps-brain serve or similar is already wired in cli.py before writing a new Dockerfile | Medium | Medium | Warning: Mitigation required - no automated recommendation available |
| Smoke test in CI requires Docker Compose available in GitHub Actions runners — verify availability or use a service container alternative | Medium | Medium | Warning: Mitigation required - no automated recommendation available |

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
