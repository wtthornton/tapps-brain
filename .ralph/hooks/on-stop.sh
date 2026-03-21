#!/bin/bash
# .ralph/hooks/on-stop.sh
# Replaces: analyze_response() in lib/response_analyzer.sh
#
# Stop hook. Runs after every Claude response. Reads response from stdin (JSON).
# Updates .ralph state files deterministically.
# Exit 0 = allow stop.

set -euo pipefail

RALPH_DIR="${CLAUDE_PROJECT_DIR:-.}/.ralph"

# Guard: only run in Ralph-managed projects
if [[ ! -d "$RALPH_DIR" ]]; then
  exit 0
fi

# Read response from stdin
INPUT=$(cat)

# Extract response text — try multiple JSON paths for compatibility
# Claude Code Stop hook input varies by mode (agent vs legacy, json vs stream-json)
response_text=""

# Try structured JSON paths first
for path in '.result' '.content' '.result.text' '.message.content' '.message.content[0].text'; do
  response_text=$(echo "$INPUT" | jq -r "$path // empty" 2>/dev/null || true)
  [[ -n "$response_text" ]] && break
done

# Fallback: if no JSON path worked, treat entire input as text
# (handles cases where Claude Code passes raw text or unknown format)
if [[ -z "$response_text" ]]; then
  response_text="$INPUT"
fi

# Parse RALPH_STATUS block fields (use grep -oP on platforms that support it, fallback to sed)
extract_field() {
  local field="$1"
  local default="$2"
  local value
  value=$(echo "$response_text" | grep "${field}:" | tail -1 | sed "s/.*${field}:[[:space:]]*//" | tr -d '[:space:]' || true)
  echo "${value:-$default}"
}

exit_signal=$(extract_field "EXIT_SIGNAL" "false")
status=$(extract_field "STATUS" "UNKNOWN")
tasks_done=$(extract_field "TASKS_COMPLETED_THIS_LOOP" "0")
files_modified_reported=$(extract_field "FILES_MODIFIED" "0")
work_type=$(extract_field "WORK_TYPE" "UNKNOWN")
recommendation=$(echo "$response_text" | grep "RECOMMENDATION:" | tail -1 | sed 's/.*RECOMMENDATION:[[:space:]]*//' || echo "")

# Count actual files modified (from PostToolUse tracking)
actual_files_modified=0
if [[ -f "$RALPH_DIR/.files_modified_this_loop" ]]; then
  actual_files_modified=$(sort -u "$RALPH_DIR/.files_modified_this_loop" | wc -l | tr -d '[:space:]')
fi

# Use the higher of reported vs actual (defense-in-depth)
files_modified=$((files_modified_reported > actual_files_modified ? files_modified_reported : actual_files_modified))

# Update loop count
loop_count=0
if [[ -f "$RALPH_DIR/status.json" ]]; then
  loop_count=$(jq -r '.loop_count // 0' "$RALPH_DIR/status.json" 2>/dev/null || echo "0")
fi
loop_count=$((loop_count + 1))

# Write status.json (atomic write via temp file)
local_tmp=$(mktemp "$RALPH_DIR/status.json.XXXXXX")
cat > "$local_tmp" <<EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "loop_count": $loop_count,
  "status": "${status}",
  "exit_signal": "${exit_signal}",
  "tasks_completed": ${tasks_done},
  "files_modified": ${files_modified},
  "work_type": "${work_type}",
  "recommendation": $(echo "${recommendation}" | jq -Rs .)
}
EOF
mv "$local_tmp" "$RALPH_DIR/status.json"

# Update circuit breaker — check for progress
if [[ "$files_modified" -gt 0 || "$tasks_done" -gt 0 ]]; then
  # Progress detected — reset no-progress counter
  if [[ -f "$RALPH_DIR/.circuit_breaker_state" ]]; then
    local_tmp=$(mktemp "$RALPH_DIR/.circuit_breaker_state.XXXXXX")
    jq '.consecutive_no_progress = 0 | .state = "CLOSED"' \
      "$RALPH_DIR/.circuit_breaker_state" > "$local_tmp" \
      && mv "$local_tmp" "$RALPH_DIR/.circuit_breaker_state"
  fi
else
  # No progress — increment counter
  if [[ -f "$RALPH_DIR/.circuit_breaker_state" ]]; then
    current=$(jq -r '.consecutive_no_progress // 0' "$RALPH_DIR/.circuit_breaker_state" 2>/dev/null || echo "0")
    threshold=${CB_NO_PROGRESS_THRESHOLD:-3}
    new_count=$((current + 1))

    local_tmp=$(mktemp "$RALPH_DIR/.circuit_breaker_state.XXXXXX")
    if [[ $new_count -ge $threshold ]]; then
      echo "Circuit breaker OPEN: $new_count loops with no progress" >&2
      jq ".consecutive_no_progress = $new_count | .state = \"OPEN\"" \
        "$RALPH_DIR/.circuit_breaker_state" > "$local_tmp" \
        && mv "$local_tmp" "$RALPH_DIR/.circuit_breaker_state"
    else
      jq ".consecutive_no_progress = $new_count" \
        "$RALPH_DIR/.circuit_breaker_state" > "$local_tmp" \
        && mv "$local_tmp" "$RALPH_DIR/.circuit_breaker_state"
    fi
  fi
fi

# Log for monitoring
echo "[$(date '+%H:%M:%S')] Loop $loop_count: status=$status exit=$exit_signal tasks=$tasks_done files=$files_modified type=$work_type" \
  >> "$RALPH_DIR/live.log"

exit 0
