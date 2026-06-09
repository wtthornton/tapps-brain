---
name: tapps-score
user-invocable: true
model: claude-haiku-4-5-20251001
description: Score a Python file across 7 quality categories and display a structured report. Use when reviewing a Python file's quality scores before a code review or pull request.
allowed-tools: mcp__tapps-mcp__tapps_score_file mcp__tapps-mcp__tapps_quick_check
argument-hint: "[file-path]"
disable-model-invocation: true
---

> **DEPRECATED (v3.11.0+):** This skill wraps a single MCP tool and adds no orchestration. Call `mcp__tapps-mcp__tapps_quick_check(file_path=...)` directly, or invoke `/tapps-finish-task` for end-of-task orchestration. Scheduled for removal in v3.12.0.

Score the specified Python file using TappsMCP:

1. Call `mcp__tapps-mcp__tapps_quick_check` with the file path to get an instant score
2. If the score is below 80, call `mcp__tapps-mcp__tapps_score_file` for the full breakdown
3. Present the results in a table: category, score (0-100), top issue per category
4. Highlight any category scoring below 70 as a priority fix
5. Suggest the single highest-impact change the developer can make
