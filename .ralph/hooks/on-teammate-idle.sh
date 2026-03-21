#!/bin/bash
# .ralph/hooks/on-teammate-idle.sh
# TeammateIdle hook. Fires when a teammate is about to go idle.
#
# Exit 0 = allow idle (teammate stops)
# Exit 2 = keep working (teammate continues — e.g., assign more tasks)

set -euo pipefail

RALPH_DIR="${CLAUDE_PROJECT_DIR:-.}/.ralph"
[[ -d "$RALPH_DIR" ]] || exit 0

INPUT=$(cat)
teammate_name=$(echo "$INPUT" | jq -r '.teammate_name // "unknown"' 2>/dev/null || echo "unknown")

# Check if there are remaining tasks in fix_plan
remaining=0
if [[ -f "$RALPH_DIR/fix_plan.md" ]]; then
  total=$(grep -c '^\- \[' "$RALPH_DIR/fix_plan.md" 2>/dev/null || echo "0")
  done=$(grep -c '^\- \[x\]' "$RALPH_DIR/fix_plan.md" 2>/dev/null || echo "0")
  remaining=$((total - done))
fi

# Log the event
echo "[$(date '+%H:%M:%S')] TEAMMATE IDLE: $teammate_name (${remaining} tasks remaining)" \
  >> "$RALPH_DIR/live.log"

# If tasks remain, could potentially reassign — but for now, allow idle
# Future enhancement: check if any remaining tasks match this teammate's scope
exit 0
