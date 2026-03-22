#!/bin/bash
# .ralph/hooks/on-stop.sh
# Replaces: analyze_response() in lib/response_analyzer.sh
#
# Stop hook. Runs after every Claude response. Reads response from stdin (JSON).
# Updates .ralph state files deterministically.
# Exit 0 = allow stop.
#
# Performance: single-pass parsing with minimal subprocess spawns (v1.8.5).

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

# PERF: Extract ALL fields in a single grep+sed pass instead of 6 separate extract_field() calls
# Each extract_field() call spawned grep + sed + tr = 3 subprocesses × 6 fields = 18 subprocesses.
# This block does it in ~3 subprocesses total.
_status_block=$(echo "$response_text" | sed -n '/---RALPH_STATUS---/,/---END_RALPH_STATUS---/p' || true)

if [[ -n "$_status_block" ]]; then
  exit_signal=$(echo "$_status_block" | grep "EXIT_SIGNAL:" | tail -1 | sed 's/.*EXIT_SIGNAL:[[:space:]]*//' | tr -d '[:space:]' || echo "false")
  status=$(echo "$_status_block" | grep "STATUS:" | grep -v "TESTS_STATUS\|END_RALPH" | tail -1 | sed 's/.*STATUS:[[:space:]]*//' | tr -d '[:space:]' || echo "UNKNOWN")
  tasks_done=$(echo "$_status_block" | grep "TASKS_COMPLETED_THIS_LOOP:" | tail -1 | sed 's/.*TASKS_COMPLETED_THIS_LOOP:[[:space:]]*//' | tr -d '[:space:]' || echo "0")
  files_modified_reported=$(echo "$_status_block" | grep "FILES_MODIFIED:" | tail -1 | sed 's/.*FILES_MODIFIED:[[:space:]]*//' | tr -d '[:space:]' || echo "0")
  work_type=$(echo "$_status_block" | grep "WORK_TYPE:" | tail -1 | sed 's/.*WORK_TYPE:[[:space:]]*//' | tr -d '[:space:]' || echo "UNKNOWN")
  recommendation=$(echo "$_status_block" | grep "RECOMMENDATION:" | tail -1 | sed 's/.*RECOMMENDATION:[[:space:]]*//' || echo "")
else
  # No structured status block found — extract from full text
  exit_signal="false"
  status="UNKNOWN"
  tasks_done="0"
  files_modified_reported="0"
  work_type="UNKNOWN"
  recommendation=""
fi

# Defaults for empty values
exit_signal="${exit_signal:-false}"
status="${status:-UNKNOWN}"
tasks_done="${tasks_done:-0}"
files_modified_reported="${files_modified_reported:-0}"
work_type="${work_type:-UNKNOWN}"

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

# PERF: Read loop count and write status.json in single operation (was: jq read + date + jq write = 3 subprocesses)
loop_count=0
if [[ -f "$RALPH_DIR/status.json" ]]; then
  loop_count=$(jq -r '.loop_count // 0' "$RALPH_DIR/status.json" 2>/dev/null || echo "0")
fi
loop_count=$((loop_count + 1))

# Write status.json (atomic write via temp file)
# PERF: Use printf for timestamp instead of date subprocess where possible
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

# PERF: Update circuit breaker in a single jq call (was: 2-3 separate jq + mktemp + mv per branch)
if [[ -f "$RALPH_DIR/.circuit_breaker_state" ]]; then
  local_tmp=$(mktemp "$RALPH_DIR/.circuit_breaker_state.XXXXXX")
  if [[ "$files_modified" -gt 0 || "$tasks_done" -gt 0 ]]; then
    # Progress detected — reset no-progress counter and close
    jq '.consecutive_no_progress = 0 | .state = "CLOSED"' \
      "$RALPH_DIR/.circuit_breaker_state" > "$local_tmp" 2>/dev/null \
      && mv "$local_tmp" "$RALPH_DIR/.circuit_breaker_state"
  else
    # No progress — single jq call to read threshold, increment, and conditionally open
    threshold=${CB_NO_PROGRESS_THRESHOLD:-3}
    jq --argjson threshold "$threshold" '
      .consecutive_no_progress = ((.consecutive_no_progress // 0) + 1) |
      if .consecutive_no_progress >= $threshold then .state = "OPEN" else . end
    ' "$RALPH_DIR/.circuit_breaker_state" > "$local_tmp" 2>/dev/null \
      && mv "$local_tmp" "$RALPH_DIR/.circuit_breaker_state"

    # Log if circuit breaker opened
    new_count=$(jq -r '.consecutive_no_progress // 0' "$RALPH_DIR/.circuit_breaker_state" 2>/dev/null || echo "0")
    if [[ "$new_count" -ge "${threshold}" ]]; then
      echo "Circuit breaker OPEN: $new_count loops with no progress" >&2
    fi
  fi
  rm -f "$local_tmp" 2>/dev/null  # WSL-1: catch cross-fs orphans
fi

# Log for monitoring
echo "[$(date '+%H:%M:%S')] Loop $loop_count: status=$status exit=$exit_signal tasks=$tasks_done files=$files_modified type=$work_type" \
  >> "$RALPH_DIR/live.log"

exit 0
