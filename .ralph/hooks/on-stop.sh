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
response_text=""
for path in '.result' '.content' '.result.text' '.message.content' '.response[0].text' '.response'; do
  response_text=$(echo "$INPUT" | jq -r "$path // empty" 2>/dev/null || true)
  [[ -n "$response_text" ]] && break
done

# Fallback: if no JSON path matched, use raw input as text
# This handles agent mode and unexpected payload formats (WSL-WINDOWS-VERSION-DIVERGENCE bug)
if [[ -z "$response_text" ]]; then
  response_text="$INPUT"
fi

# STREAM-3: If response_text contains JSON-escaped RALPH_STATUS block (literal \n instead of
# newlines), unescape it so grep-based field extraction works correctly.
# This happens when the response arrives from JSONL stream extraction.
if echo "$response_text" | grep -q '\\n.*RALPH_STATUS' 2>/dev/null; then
  response_text=$(printf '%b' "$response_text")
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

# STREAM-3: Fallback inference — if WORK_TYPE is UNKNOWN but files were modified, infer IMPLEMENTATION
if [[ "$work_type" == "UNKNOWN" && "$files_modified_reported" -gt 0 ]]; then
  work_type="IMPLEMENTATION"
fi

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
rm -f "$local_tmp" 2>/dev/null  # WSL-1: catch cross-fs copy+unlink orphans

# Update circuit breaker — check for progress
if [[ "$files_modified" -gt 0 || "$tasks_done" -gt 0 ]]; then
  # Progress detected — reset no-progress counter
  if [[ -f "$RALPH_DIR/.circuit_breaker_state" ]]; then
    local_tmp=$(mktemp "$RALPH_DIR/.circuit_breaker_state.XXXXXX")
    jq '.consecutive_no_progress = 0 | .state = "CLOSED"' \
      "$RALPH_DIR/.circuit_breaker_state" > "$local_tmp" \
      && mv "$local_tmp" "$RALPH_DIR/.circuit_breaker_state"
    rm -f "$local_tmp" 2>/dev/null  # WSL-1: catch cross-fs orphans
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
      rm -f "$local_tmp" 2>/dev/null  # WSL-1: catch cross-fs orphans
    else
      jq ".consecutive_no_progress = $new_count" \
        "$RALPH_DIR/.circuit_breaker_state" > "$local_tmp" \
        && mv "$local_tmp" "$RALPH_DIR/.circuit_breaker_state"
      rm -f "$local_tmp" 2>/dev/null  # WSL-1: catch cross-fs orphans
    fi
  fi
fi

# Log for monitoring
echo "[$(date '+%H:%M:%S')] Loop $loop_count: status=$status exit=$exit_signal tasks=$tasks_done files=$files_modified type=$work_type" \
  >> "$RALPH_DIR/live.log"

exit 0
