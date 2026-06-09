---
name: tapps-score
description: Score a Python file across 7 quality categories and display a structured report. Use when reviewing a Python file's quality scores before a code review or pull request.
mcp_tools:
  - tapps_score_file
  - tapps_quick_check
---

> **DEPRECATED (v3.11.0+):** Wraps a single MCP tool with no orchestration. Call `tapps_quick_check` directly or invoke the `tapps-finish-task` skill. Scheduled for removal in v3.12.0.

Score the specified Python file using TappsMCP:

1. Call `tapps_quick_check` with the file path to get an instant score
2. If the score is below 80, call `tapps_score_file` for the full 7-category breakdown
3. Present the results in a table: category, score (0-100), top issue per category
4. Highlight any category scoring below 70 as a priority fix
5. Suggest the single highest-impact change the developer can make
