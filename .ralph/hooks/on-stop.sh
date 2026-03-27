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
  # Only convert literal \n to newlines (not all backslash sequences like \t, \x, etc.)
  response_text=$(echo "$response_text" | sed 's/\\n/\n/g')
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

# Validate numeric fields to prevent arithmetic errors from non-numeric Claude output
[[ "$tasks_done" =~ ^[0-9]+$ ]] || tasks_done=0
[[ "$files_modified_reported" =~ ^[0-9]+$ ]] || files_modified_reported=0

# STREAM-3: Fallback inference — if WORK_TYPE is UNKNOWN but files were modified, infer IMPLEMENTATION
if [[ "$work_type" == "UNKNOWN" && "$files_modified_reported" -gt 0 ]]; then
  work_type="IMPLEMENTATION"
fi

# USYNC-1: Detect question patterns in response text (upstream #190)
# When Claude asks questions in headless mode, it's not making progress but it's
# also not stuck — corrective guidance (USYNC-2) will redirect next loop.
QUESTION_PATTERNS=(
    "should I"
    "would you"
    "do you want"
    "which approach"
    "which option"
    "how should"
    "what should"
    "shall I"
    "do you prefer"
    "can you clarify"
    "could you"
    "what do you think"
    "please confirm"
    "need clarification"
    "awaiting.*input"
    "waiting.*response"
    "your preference"
)

asking_questions="false"
question_count=0
if [[ -n "$response_text" ]]; then
  for pattern in "${QUESTION_PATTERNS[@]}"; do
    count=$(echo "$response_text" | grep -ciE "$pattern" 2>/dev/null || echo "0")
    question_count=$((question_count + count))
  done
  [[ "$question_count" -gt 0 ]] && asking_questions="true"
fi

# USYNC-4: Detect permission denials in response (upstream #101)
# Permission denials are deterministic — they won't self-resolve on retry.
has_permission_denials="false"
permission_denial_count=0
if [[ -n "$response_text" ]]; then
  permission_denial_count=$(echo "$response_text" | grep -ciE '(permission denied|tool not allowed|not in allowed|disallowed tool|not permitted)' 2>/dev/null || echo "0")
  [[ "$permission_denial_count" -gt 0 ]] && has_permission_denials="true"
fi

# Count actual files modified (from PostToolUse tracking)
actual_files_modified=0
if [[ -f "$RALPH_DIR/.files_modified_this_loop" ]]; then
  actual_files_modified=$(sort -u "$RALPH_DIR/.files_modified_this_loop" | wc -l | tr -d '[:space:]')
fi

# Use the higher of reported vs actual (defense-in-depth)
files_modified=$((files_modified_reported > actual_files_modified ? files_modified_reported : actual_files_modified))

# PLANOPT: Mark import graph stale if new source files were created this loop
# This enables the optimizer to rebuild the graph on next session start
if [[ -f "$RALPH_DIR/.files_modified_this_loop" && -f "$RALPH_DIR/.import_graph.json" ]]; then
  while IFS= read -r _modified_file; do
    [[ -z "$_modified_file" ]] && continue
    # Only check source files (not config, docs, etc.)
    if echo "$_modified_file" | grep -qE '\.(py|ts|tsx|js|jsx|sh)$'; then
      # If this file didn't exist at HEAD, it's newly created → graph is stale
      if ! git show HEAD:"$_modified_file" &>/dev/null 2>&1; then
        touch "$RALPH_DIR/.import_graph.json.stale" 2>/dev/null || true
        break  # One new file is enough to trigger rebuild
      fi
    fi
  done < "$RALPH_DIR/.files_modified_this_loop"
fi

# PERF: Read loop count and write status.json in single operation (was: jq read + date + jq write = 3 subprocesses)
loop_count=0
if [[ -f "$RALPH_DIR/status.json" ]]; then
  loop_count=$(jq -r '.loop_count // 0' "$RALPH_DIR/status.json" 2>/dev/null || echo "0")
fi
# Validate loop_count is numeric before arithmetic
[[ "$loop_count" =~ ^[0-9]+$ ]] || loop_count=0
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
  "recommendation": $(echo "${recommendation}" | jq -Rs .),
  "asking_questions": ${asking_questions},
  "question_count": ${question_count},
  "has_permission_denials": ${has_permission_denials},
  "permission_denial_count": ${permission_denial_count}
}
EOF
mv "$local_tmp" "$RALPH_DIR/status.json"
rm -f "$local_tmp" 2>/dev/null  # WSL-1: catch cross-fs copy+unlink orphans

# PERF: Update circuit breaker in a single jq call (was: 2-3 separate jq + mktemp + mv per branch)
if [[ -f "$RALPH_DIR/.circuit_breaker_state" ]]; then
  local_tmp=$(mktemp "$RALPH_DIR/.circuit_breaker_state.XXXXXX")
  if [[ "$files_modified" -gt 0 || "$tasks_done" -gt 0 ]]; then
    # Progress detected — reset no-progress counter, permission denials, and close
    jq '.consecutive_no_progress = 0 | .consecutive_permission_denials = 0 | .state = "CLOSED"' \
      "$RALPH_DIR/.circuit_breaker_state" > "$local_tmp" 2>/dev/null \
      && mv "$local_tmp" "$RALPH_DIR/.circuit_breaker_state"
  elif [[ "$asking_questions" == "true" ]]; then
    # USYNC-3: Claude is asking questions — not progress, but not stagnation either.
    # Suppress no-progress counter; corrective guidance (USYNC-2) will redirect next loop.
    # Also reset permission denial counter (questions aren't denials).
    jq '.consecutive_permission_denials = 0' \
      "$RALPH_DIR/.circuit_breaker_state" > "$local_tmp" 2>/dev/null \
      && mv "$local_tmp" "$RALPH_DIR/.circuit_breaker_state"
  elif [[ "$has_permission_denials" == "true" ]]; then
    # USYNC-4: Permission denials are deterministic — trip fast with lower threshold.
    pd_threshold=${CB_PERMISSION_DENIAL_THRESHOLD:-2}
    jq --argjson pd_threshold "$pd_threshold" '
      .consecutive_permission_denials = ((.consecutive_permission_denials // 0) + 1) |
      .consecutive_no_progress = ((.consecutive_no_progress // 0) + 1) |
      if .consecutive_permission_denials >= $pd_threshold then
        .state = "OPEN" | .total_opens = ((.total_opens // 0) + 1) | .opened_at = (now | todate) |
        .reason = ("Permission denied " + (.consecutive_permission_denials | tostring) + " consecutive times")
      else . end
    ' "$RALPH_DIR/.circuit_breaker_state" > "$local_tmp" 2>/dev/null \
      && mv "$local_tmp" "$RALPH_DIR/.circuit_breaker_state"

    # Log if circuit breaker opened from permission denials
    new_pd=$(jq -r '.consecutive_permission_denials // 0' "$RALPH_DIR/.circuit_breaker_state" 2>/dev/null || echo "0")
    if [[ "$new_pd" -ge "${pd_threshold}" ]]; then
      echo "Circuit breaker OPEN: permission denied $new_pd consecutive times" >&2
    fi
  else
    # No progress — single jq call to read threshold, increment, and conditionally open
    # LOGFIX-8: Also increment total_opens when transitioning to OPEN
    threshold=${CB_NO_PROGRESS_THRESHOLD:-3}
    jq --argjson threshold "$threshold" '
      .consecutive_no_progress = ((.consecutive_no_progress // 0) + 1) |
      .consecutive_permission_denials = 0 |
      if .consecutive_no_progress >= $threshold then
        .state = "OPEN" | .total_opens = ((.total_opens // 0) + 1) | .opened_at = (now | todate)
      else . end
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
log_line="[$(date '+%H:%M:%S')] Loop $loop_count: status=$status exit=$exit_signal tasks=$tasks_done files=$files_modified type=$work_type"
[[ "$asking_questions" == "true" ]] && log_line+=" questions=$question_count"
[[ "$has_permission_denials" == "true" ]] && log_line+=" denials=$permission_denial_count"
echo "$log_line" >> "$RALPH_DIR/live.log"

# Detailed logging to ralph.log for key decisions
_ralph_log="$RALPH_DIR/logs/ralph.log"
if [[ -f "$_ralph_log" ]]; then
  _ts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "")
  # Log status block parsing result
  if [[ -z "$_status_block" ]]; then
    echo "[$_ts] [WARN] on-stop: No RALPH_STATUS block found in response" >> "$_ralph_log"
  fi
  # Log question detection
  if [[ "$asking_questions" == "true" ]]; then
    echo "[$_ts] [INFO] on-stop: Detected $question_count question pattern(s) in response (USYNC-1)" >> "$_ralph_log"
  fi
  # Log permission denial detection
  if [[ "$has_permission_denials" == "true" ]]; then
    echo "[$_ts] [WARN] on-stop: Detected $permission_denial_count permission denial(s) in response (USYNC-4)" >> "$_ralph_log"
  fi
  # Log circuit breaker state transitions
  if [[ -f "$RALPH_DIR/.circuit_breaker_state" ]]; then
    _cb_state=$(jq -r '.state // "CLOSED"' "$RALPH_DIR/.circuit_breaker_state" 2>/dev/null || echo "CLOSED")
    _cb_np=$(jq -r '.consecutive_no_progress // 0' "$RALPH_DIR/.circuit_breaker_state" 2>/dev/null || echo "0")
    if [[ "$_cb_state" == "OPEN" ]]; then
      _cb_reason=$(jq -r '.reason // "no progress threshold"' "$RALPH_DIR/.circuit_breaker_state" 2>/dev/null || echo "unknown")
      echo "[$_ts] [ERROR] on-stop: Circuit breaker OPEN ($_cb_reason)" >> "$_ralph_log"
    elif [[ "$files_modified" -gt 0 || "$tasks_done" -gt 0 ]]; then
      echo "[$_ts] [INFO] on-stop: Progress detected (tasks=$tasks_done files=$files_modified) — circuit breaker reset" >> "$_ralph_log"
    elif [[ "$_cb_np" -gt 0 ]]; then
      echo "[$_ts] [WARN] on-stop: No progress — consecutive_no_progress=$_cb_np" >> "$_ralph_log"
    fi
  fi
  # Log import graph staleness
  if [[ -f "$RALPH_DIR/.import_graph.json.stale" ]]; then
    echo "[$_ts] [INFO] on-stop: Import graph marked stale (new source files created)" >> "$_ralph_log"
  fi
fi

exit 0
