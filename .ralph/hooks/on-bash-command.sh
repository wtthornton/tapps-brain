#!/bin/bash
# .ralph/hooks/on-bash-command.sh
# PostToolUse hook for Bash commands. Logs executed commands.

set -euo pipefail

RALPH_DIR="${CLAUDE_PROJECT_DIR:-.}/.ralph"
[[ -d "$RALPH_DIR" ]] || exit 0

# Logging only — no action needed for now
exit 0
