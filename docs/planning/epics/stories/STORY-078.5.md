# Story 78.5 — tapps-visual /healthz liveness endpoint

**Points:** 2 | **Epic:** EPIC-078 | **Priority:** P2

## What

Add `GET /healthz` on the tapps-visual nginx container returning 200 when static files are served (independent of brain upstream).

## Where

- `docker/nginx-visual.conf`
- `docker/docker-compose.hive.yaml` — optional healthcheck for tapps-visual

## Why

Currently `GET :8088/healthz` returns **404**. Operators cannot distinguish "visual container down" from "brain upstream slow".

## Acceptance Criteria

- [ ] `GET http://localhost:8088/healthz` → 200 `{"ok":true,"service":"tapps-visual"}`
- [ ] Endpoint does not proxy to tapps-brain-http
- [ ] Optional compose healthcheck added for tapps-visual service
