#!/bin/bash
# .ralph/hooks/on-subagent-done.sh
# SubagentStop hook. Logs sub-agent completion for monitoring.
#
# stdin: JSON with sub-agent result data
# Exit 0 = allow (normal)

set -euo pipefail

RALPH_DIR="${CLAUDE_PROJECT_DIR:-.}/.ralph"
[[ -d "$RALPH_DIR" ]] || exit 0

INPUT=$(cat)

# PERF: Extract all fields in single jq call (was: 4 separate jq calls)
read -r agent_name agent_id duration_ms error < <(
  echo "$INPUT" | jq -r '[
    (.agent_name // .subagent_type // "unknown"),
    (.agent_id // "unknown"),
    (.duration_ms // 0 | tostring),
    (.error // "")
  ] | @tsv' 2>/dev/null || echo "unknown unknown 0 "
)

# Calculate duration in seconds
duration_s=0
if [[ "$duration_ms" -gt 0 ]] 2>/dev/null; then
  duration_s=$((duration_ms / 1000))
fi

# Log completion
if [[ -n "$error" ]]; then
  echo "[$(date '+%H:%M:%S')] SUBAGENT FAILED: $agent_name (id=$agent_id) after ${duration_s}s — $error" \
    >> "$RALPH_DIR/live.log"
else
  echo "[$(date '+%H:%M:%S')] SUBAGENT DONE: $agent_name (id=$agent_id) in ${duration_s}s" \
    >> "$RALPH_DIR/live.log"
fi

exit 0
