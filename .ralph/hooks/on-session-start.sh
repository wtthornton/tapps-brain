#!/bin/bash
# .ralph/hooks/on-session-start.sh
# Replaces: build_loop_context() in ralph_loop.sh
#
# SessionStart hook. Reads loop state and emits context for Claude's system prompt.
# Exit 0 = allow session. stderr = inject into context.

set -euo pipefail

RALPH_DIR="${CLAUDE_PROJECT_DIR:-.}/.ralph"

# Guard: only run if this is a Ralph-managed project
if [[ ! -d "$RALPH_DIR" ]]; then
  exit 0
fi

# Read current loop count
loop_count=0
if [[ -f "$RALPH_DIR/status.json" ]]; then
  loop_count=$(jq -r '.loop_count // 0' "$RALPH_DIR/status.json" 2>/dev/null || echo "0")
fi

# Read fix_plan completion status
total_tasks=0
done_tasks=0
if [[ -f "$RALPH_DIR/fix_plan.md" ]]; then
  total_tasks=$(grep -c '^\- \[' "$RALPH_DIR/fix_plan.md" 2>/dev/null || echo "0")
  done_tasks=$(grep -c '^\- \[x\]' "$RALPH_DIR/fix_plan.md" 2>/dev/null || echo "0")
fi
remaining=$((total_tasks - done_tasks))

# Read circuit breaker state
cb_state="CLOSED"
if [[ -f "$RALPH_DIR/.circuit_breaker_state" ]]; then
  cb_state=$(jq -r '.state // "CLOSED"' "$RALPH_DIR/.circuit_breaker_state" 2>/dev/null || echo "CLOSED")
fi

# Read last loop outcome (if available)
last_status=""
if [[ -f "$RALPH_DIR/status.json" ]]; then
  last_status=$(jq -r '.status // ""' "$RALPH_DIR/status.json" 2>/dev/null || echo "")
fi

# Clear per-loop file tracking
: > "$RALPH_DIR/.files_modified_this_loop" 2>/dev/null || true

# Emit context to stderr (injected into Claude's system prompt)
cat >&2 <<EOF
Ralph loop #$((loop_count + 1)). Tasks: $done_tasks/$total_tasks complete, $remaining remaining.
Circuit breaker: $cb_state.$([ -n "$last_status" ] && echo " Last loop: $last_status.")
Read .ralph/fix_plan.md and do the FIRST unchecked item.
EOF

exit 0
