# Agent notes (Cursor / automation)

Short entry points for AI assistants working in this repo.

- **Commands & quality bar:** `.cursor/rules/project.mdc` (also mirrored in `CLAUDE.md`).
- **Architecture & schema:** `docs/engineering/` — start with `docs/engineering/README.md`. Full doc map: `docs/DOCUMENTATION_INDEX.md`.
- **Setup:** `uv sync --group dev` (creates `.venv` at repo root; Python 3.12+). Dev deps live in `[dependency-groups]` in `pyproject.toml`, not `[project.optional-dependencies]`.
- **Tests:** `pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95` — or run the default test task from VS Code/Cursor (**Terminal → Run Task → pytest**).
- **Cursor MCP / indexing:** `.cursor/mcp.json` — tapps-mcp, docs-mcp (sibling `tapps-mcp` checkout), Playwright; `.cursorignore` trims bulky dirs — see `project.mdc` § Cursor.
- **Delivery priorities (non-Ralph):** `docs/planning/open-issues-roadmap.md`.

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
