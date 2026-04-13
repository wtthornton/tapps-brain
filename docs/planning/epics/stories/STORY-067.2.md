# Story 67.2 -- Fix tapps-visual nginx upstream and validate /snapshot end-to-end

<!-- docsmcp:start:user-story -->

> **As a** operator running the hive stack, **I want** the brain-visual dashboard to display live memory data via the /snapshot proxy, **so that** I can observe real hive state without running a separate export command

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** S

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the acceptance criteria below are met and the feature is delivered. Refine this paragraph to state why this story exists and what it enables.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

nginx-visual.conf currently proxies /snapshot to tapps-brain-mcp:8080 — a hostname that was never added to the compose network. After STORY-067.1 lands, the correct hostname is tapps-brain-http. This story updates the nginx upstream hostname, removes the epoch-0 placeholder brain-visual.json that is baked into the tapps-visual image (it shows hive_attached: false and entry_count: 0 permanently), and documents that the live /snapshot feed replaces the static file.

See [Epic 67](../EPIC-067.md) for project context and shared definitions.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `docker/nginx-visual.conf`
- `docker/Dockerfile.visual`
- `docker/README.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Change proxy_pass upstream in nginx-visual.conf from tapps-brain-mcp:8080 to tapps-brain-http:8080 (`docker/nginx-visual.conf`)
- [ ] Remove the COPY examples/brain-visual/brain-visual.json line from Dockerfile.visual (or replace with a stub that redirects to /snapshot) (`docker/Dockerfile.visual`)
- [ ] Update docker/README.md to document that brain-visual.json is no longer baked in — the dashboard fetches /snapshot live from tapps-brain-http (`docker/README.md`)
- [ ] Manual integration test: boot full stack, open http://localhost:8088 in browser, verify dashboard loads data from /snapshot (not the placeholder) (`docker/docker-compose.hive.yaml`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] nginx upstream hostname in nginx-visual.conf is tapps-brain-http:8080 (not tapps-brain-mcp)
- [ ] GET http://localhost:8088/snapshot proxied through nginx returns 200 with a non-placeholder VisualSnapshot JSON (generated_at is not 1970-01-01)
- [ ] The epoch-0 placeholder brain-visual.json is not baked into the tapps-visual image
- [ ] docker/README.md explains the live /snapshot feed and how to fall back to a static export if tapps-brain-http is not running

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

Definition of Done per [Epic 67](../EPIC-067.md).

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_nginx_upstream_hostname_nginxvisualconf_tappsbrainhttp8080_not` -- nginx upstream hostname in nginx-visual.conf is tapps-brain-http:8080 (not tapps-brain-mcp)
2. `test_ac2_get_httplocalhost8088snapshot_proxied_through_nginx_returns_200` -- GET http://localhost:8088/snapshot proxied through nginx returns 200 with a non-placeholder VisualSnapshot JSON (generated_at is not 1970-01-01)
3. `test_ac3_epoch0_placeholder_brainvisualjson_not_baked_into_tappsvisual_image` -- The epoch-0 placeholder brain-visual.json is not baked into the tapps-visual image
4. `test_ac4_dockerreadmemd_explains_live_snapshot_feed_how_fall_back_static_export` -- docker/README.md explains the live /snapshot feed and how to fall back to a static export if tapps-brain-http is not running

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- The static brain-visual.json was added as a fallback but it creates a worse experience than a 503 because it looks like the dashboard is working while showing zero data. Better to let nginx return 502/503 when the upstream is down so the operator knows to start the service.
- If brain-visual.demo.json (already in examples/) is a reasonable offline fallback
- consider serving that under a /demo route instead.

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-067.1 (tapps-brain-http service must exist on the compose network)

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
