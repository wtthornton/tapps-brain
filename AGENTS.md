# Agent notes (Cursor / automation)

Short entry points for AI assistants working in this repo.

- **Commands & quality bar:** `.cursor/rules/project.mdc` (also mirrored in `CLAUDE.md`).
- **Architecture & schema:** `docs/engineering/` — start with `docs/engineering/README.md`. Full doc map: `docs/DOCUMENTATION_INDEX.md`.
- **Setup:** `uv sync --extra dev` (use `.venv` at repo root; Python 3.12+).
- **Tests:** `pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95` — or run the default test task from VS Code/Cursor (**Terminal → Run Task → pytest**).
- **Cursor MCP / indexing:** `.cursor/mcp.json` — tapps-mcp, docs-mcp (sibling `tapps-mcp` checkout), Playwright; `.cursorignore` trims bulky dirs — see `project.mdc` § Cursor.
- **Delivery priorities (non-Ralph):** `docs/planning/open-issues-roadmap.md`.
