---
id: EPIC-022
title: "Code Review — Interfaces (MCP, CLI, IO)"
status: done
priority: medium
created: 2026-03-22
target_date: 2026-05-15
tags: [review, mcp, cli, io, quality]
---

# EPIC-022: Code Review — Interfaces (MCP, CLI, IO)

## Context

Full code review of all user-facing interfaces: MCP server (54 tools), CLI (41 commands), IO, and markdown import.

## Success Criteria

- [x] `mcp_server.py` reviewed (all 3 sections: core tools, Hive/graph/audit, config/agent/profile)
- [x] `cli.py` reviewed (both halves: core and advanced commands)
- [x] `io.py` reviewed (import/export)
- [x] `markdown_import.py` reviewed (markdown parsing)
- [x] All issues found are fixed with tests

## Stories

See `.ralph/fix_plan.md` tasks 022-A through 022-G.
