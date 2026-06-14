#!/usr/bin/env bash
# tapps-mcp-hook-version: 3.12.27
# tapps-mcp-hook-content-sha: fcc22818
# TappsMCP SubagentStart hook
# Injects TappsMCP awareness into spawned subagents.
INPUT=$(cat)
echo "[TappsMCP] This project uses TappsMCP for code quality."
echo "MCP tools: tapps_quick_check, tapps_score_file, tapps_validate_changed, tapps_memory."
exit 0
