# Agent notes (Cursor / automation)

Short entry points for AI assistants working in this repo.

- **Commands & quality bar:** `.cursor/rules/project.mdc` (also mirrored in `CLAUDE.md`).
- **Architecture & schema:** `docs/engineering/` — start with `docs/engineering/README.md`. Full doc map: `docs/DOCUMENTATION_INDEX.md`.
- **Setup:** `uv sync --group dev` (creates `.venv` at repo root; Python 3.12+). Dev deps live in `[dependency-groups]` in `pyproject.toml`, not `[project.optional-dependencies]`.
- **Tests:** `pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95` — or run the default test task from VS Code/Cursor (**Terminal → Run Task → pytest**).
- **Cursor MCP / indexing:** `.cursor/mcp.json` — tapps-mcp, docs-mcp (sibling `tapps-mcp` checkout), Playwright; `.cursorignore` trims bulky dirs — see `project.mdc` § Cursor.
- **Delivery priorities (non-Ralph):** `docs/planning/open-issues-roadmap.md`.

## Quick start: clone → compose → pytest (≤ 15 min)

```bash
# 1. Clone and install
git clone https://github.com/your-org/tapps-brain
cd tapps-brain
uv sync --group dev           # creates .venv; Python 3.12+ required

# 2. Start Postgres + pgvector (Docker required)
make brain-up                 # pulls pgvector/pgvector:pg17, waits for ready

# 3. Run the full test suite
make brain-test               # pytest with coverage gate ≥ 95 %

# 4. Tear down when done
make brain-down               # removes containers + volumes
```

Expected total time: ~5–12 min depending on image pull and hardware.

### All Makefile targets

| Target | Description |
|---|---|
| `make brain-up` | Start Postgres+pgvector in the background |
| `make brain-down` | Stop containers and remove volumes |
| `make brain-restart` | Restart the Postgres container (keeps data) |
| `make brain-psql` | Open a psql shell in the running container |
| `make brain-test` | Full test suite with coverage (≥ 95 %) |
| `make brain-test-fast` | Tests excluding benchmarks, no coverage, fail-fast (`-x`) |
| `make brain-lint` | Ruff lint + format check |
| `make brain-type` | Strict mypy type check |
| `make brain-qa` | Full QA: lint + type + tests (mirrors CI) |

### DSN override

The default dev DSN is `postgres://tapps:tapps@localhost:5432/tapps_dev`.
Override with:

```bash
make brain-test TAPPS_DEV_DSN="postgres://me:pw@myhost:5432/tapps"
```

See `docs/guides/postgres-dsn.md` for the full env-var reference.

### CI

GitHub Actions (`ci.yml`) runs the same `pytest` command against a
`pgvector/pgvector:pg17` service container on every push and PR — no Docker
needed locally just for CI. The `TAPPS_TEST_POSTGRES_DSN` env var is set
automatically in CI.

## Ralph (autonomous loop — Linux / Ubuntu)

Run commands from the **repository root** (the directory that contains `pyproject.toml`). Do **not** type a literal path like `/path/to/tapps-brain` — that is only a placeholder in generic docs. Use your real clone path, for example:

```bash
cd ~/code/tapps-brain          # or: cd /home/you/your-clone/tapps-brain
test -f pyproject.toml && echo "OK: repo root" || echo "Wrong directory"
uv sync --group dev
export PATH="$HOME/.local/bin:$PATH"   # so `ralph` and `claude` resolve if installed there
claude --version
ralph                                  # or: ralph --live
```

Ralph reads `.ralph/fix_plan.md` and `.ralph/PROMPT.md`. Logs: `.ralph/logs/`. Full detail: `CLAUDE.md` § Ralph.

## v3 Load Smoke (concurrent-agent benchmark)

Validates that N concurrent agents can write and recall memories without interference against one
Postgres. Results are **informational only** (pre-SLO) — no hard latency budget is enforced in v3.0.

```bash
# Requires a running Postgres with private-memory schema applied
export TAPPS_TEST_POSTGRES_DSN="postgres://tapps:tapps@localhost:5432/tapps_test"

# 10 agents × 50 ops each (default)
python scripts/load_smoke.py

# Custom: 20 agents × 100 ops
python scripts/load_smoke.py --agents 20 --ops 100

# Without Postgres (in-memory store only, no DSN required)
python scripts/load_smoke.py --no-postgres
```

Outputs a latency table (p50/p90/p95/p99/max for save, recall, and per-agent wall time).
Full parity doc: `docs/engineering/v3-behavioral-parity.md`.
