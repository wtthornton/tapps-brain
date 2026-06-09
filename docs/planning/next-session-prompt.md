# Next session — agent handoff prompt

Copy everything below the line into a new chat (or Ralph task) as the **user message**.

---

**Project:** tapps-brain — PostgreSQL-backed memory for AI assistants (pgvector HNSW + tsvector hybrid search, Hive federation, Streamable HTTP MCP at `:8080`, `AgentBrain` facade, async-native HTTP write/recall path). Postgres-only (ADR-007).

**Start by reading:** `CLAUDE.md`, `docs/planning/STATUS.md`, then the epic you implement (if any). **Canonical queue:** [tapps-brain Linear project](https://linear.app/tappscodingagents/project/tapps-brain-e5604347c7db) — **zero open issues as of 2026-06-09**. File new work via `linear-issue` skill before coding. Ralph loop only: `.ralph/fix_plan.md`.

**Package version:** `3.24.0` (`pyproject.toml`).

**Already on `main` — do not redo:**

- **EPIC-066–070** — Postgres production readiness, multi-tenant `project_id`, Streamable HTTP + FastAPI adapter + service layer (all Done in Linear).
- **EPIC-071** — `TappsBrainClient` / `AsyncTappsBrainClient` SDK hardening, typed exceptions, retry, `docs/guides/client.md` (Done).
- **EPIC-072** — Async-native Postgres core: `AsyncConnectionPool`, `AsyncPostgresPrivateBackend`, async HTTP save/recall/reinforce paths (Done).
- **EPIC-073** — Per-profile MCP tool filtering (`X-Brain-Profile`, `coder`/`reviewer`/`full` profiles, contract tests). **Code Done** — Phase 2/3 rollout is ops (see `docs/planning/epics/EPIC-073.md`).
- **EPIC-074** — `brain_query_events` + `POST /v1/experience:query`; migration 023; `EntitySpec` type/id shorthand (v3.24.0).
- **EPIC-075** — `brain_profile_set/get` + REST profile data endpoints; migration 024 `profile_scoped_data` (v3.24.0).
- **TAP-2981** — `GET /v1/skill` returns version-matched SKILL.md for HTTP-only clients (v3.24.0).
- **EPIC-032** — OTel GenAI semantic conventions (Done — [TAP-807](https://linear.app/tappscodingagents/issue/TAP-807)).
- **TAP-2755** — June 2026 quality audit (store.py mixin split, bandit triage, doc drift) — Done.

**Backlog-by-default (execute only if a trigger fires):**

- **Extra save-path observability** beyond ADR-006 — trigger (a): save-latency incident.
- **EPIC-042 eval/GitHub hygiene** — trigger (b): milestone or stakeholder requires epic closure.
- **In-product NLI/async conflict wiring** — trigger (c): explicit product requirement (never on sync `save`).

**When Linear backlog is empty (current state):**

| If product needs… | Action |
|-------------------|--------|
| New feature | Scorecard + epic spec + Linear issue via `linear-issue` skill |
| EPIC-073 benefit in production | **Done here** — `.mcp.json` + `.cursor/mcp.json` use `coder`; monitor `/metrics` before default flip |
| Performance tail | File Linear issue first (e.g. async-native `search`/`knn_search` in `AsyncMemoryStore`) |

**Do not start without a Linear driver:** EPIC-066 stories 66.6–66.13 (all Done in Linear), further `store.py` splits, or net-new APIs.

**Your task:** If the user assigned a Linear issue, implement that slice only. Otherwise ask what to file in Linear or pick an operational task from EPIC-073 rollout. After shipping, update the epic file, `STATUS.md`, and refresh **this file**.

**Quality bar:** `ruff check` / `ruff format` on touched paths; `mypy --strict src/tapps_brain/` on touched modules; `bash scripts/release-ready.sh` before tagging. Tests: `make brain-test` or `pytest tests/ -m "not benchmark" --cov-fail-under=95`.

---

*File purpose: paste-the-prompt handoff. Last synced: 2026-06-09 — v3.24.0 wave-2 APIs shipped; Linear backlog empty.*
