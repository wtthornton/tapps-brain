#!/usr/bin/env bash
# tapps-mcp-hook-version: 3.12.42
# tapps-mcp-hook-content-sha: 79993fb0
# TappsMCP SubagentStart hook
# Injects TappsMCP awareness into spawned subagents.
INPUT=$(cat)
echo "[TappsMCP] This project uses TappsMCP for code quality."
echo "Tools: tapps_quick_check, tapps_score_file, tapps_validate_changed. Memory: uv run tapps-mcp memory …; tapps_memory on nlt-memory when enabled (TAP-3895)."
exit 0
