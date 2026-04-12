#!/bin/bash
# .ralph/hooks/on-file-change.sh
# PostToolUse hook for Edit/Write. Tracks modified files per loop.

set -euo pipefail

RALPH_DIR="${CLAUDE_PROJECT_DIR:-.}/.ralph"
[[ -d "$RALPH_DIR" ]] || exit 0

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""' 2>/dev/null || echo "")

if [[ -n "$FILE_PATH" ]]; then
  echo "$FILE_PATH" >> "$RALPH_DIR/.files_modified_this_loop"
fi

exit 0
