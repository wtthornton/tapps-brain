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
