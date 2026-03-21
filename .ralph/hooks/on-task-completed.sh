#!/bin/bash
# .ralph/hooks/on-task-completed.sh
# TaskCompleted hook. Fires when a task is marked complete.
#
# Exit 0 = allow completion
# Exit 2 = prevent completion (e.g., validation failed)

set -euo pipefail

RALPH_DIR="${CLAUDE_PROJECT_DIR:-.}/.ralph"
[[ -d "$RALPH_DIR" ]] || exit 0

INPUT=$(cat)
task_description=$(echo "$INPUT" | jq -r '.task_description // "unknown"' 2>/dev/null || echo "unknown")

# Log the completion
echo "[$(date '+%H:%M:%S')] TASK COMPLETED: $task_description" \
  >> "$RALPH_DIR/live.log"

# Allow completion
exit 0
