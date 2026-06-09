---
name: tapps-gate
description: Run a quality gate check and report pass/fail with blocking issues. Use when checking if a Python file passes the quality threshold before declaring a task complete.
mcp_tools:
  - tapps_quality_gate
---

> **DEPRECATED (v3.11.0+):** Wraps a single MCP tool with no orchestration. Call `tapps_quality_gate` directly or invoke the `tapps-finish-task` skill. Scheduled for removal in v3.12.0.

Run a quality gate check using TappsMCP:

1. Call `tapps_quality_gate` with the current project
2. Display the overall pass/fail result clearly
3. List each failing criterion with its actual vs. required value
4. If the gate fails, list the minimum changes required to pass
5. Do not declare work complete if the gate has not passed
