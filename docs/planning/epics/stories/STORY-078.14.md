# Story 78.14 — brain-visual-smoke-live + CI gate

**Points:** 5 | **Epic:** EPIC-078 | **Priority:** P0

## What

Add `scripts/brain_visual_smoke_live.sh` and `make brain-visual-smoke-live` asserting `:8088/` HTML, `:8088/snapshot` JSON schema, and `:8080/snapshot` direct path. Extend `brain_smoke_live.sh` with `/snapshot` check.

## Where

- `scripts/brain_visual_smoke_live.sh` (new)
- `scripts/brain_smoke_live.sh` — add `/snapshot` assertions
- `Makefile` — new target
- `.github/workflows/hive-smoke.yml` or new workflow job

## Tasks

- [ ] Assert `GET :8088/` contains `tapps-snapshot-url`
- [ ] Assert `GET :8088/snapshot` returns 200, `schema_version>=2`, `fingerprint_sha256` present, build time <30s
- [ ] Assert `GET :8080/snapshot` with Bearer token same schema
- [ ] Wire into CI (optional job on schedule or post-deploy)
- [ ] Document in `AGENTS.md` Makefile table

## Acceptance Criteria

- [ ] `make brain-visual-smoke-live` exits 0 against healthy local stack
- [ ] Fails with actionable message when visual up but brain down
- [ ] `brain_smoke_live.sh` includes `/snapshot` latency + schema check
