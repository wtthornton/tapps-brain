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
  # LINEAR-DASH: optional Linear-driven fields. Absent in file-mode projects.
  linear_issue=$(echo "$_status_block" | grep "LINEAR_ISSUE:" | tail -1 | sed 's/.*LINEAR_ISSUE:[[:space:]]*//' | tr -d '[:space:]' || echo "")
  linear_url=$(echo "$_status_block" | grep "LINEAR_URL:" | tail -1 | sed 's/.*LINEAR_URL:[[:space:]]*//' | tr -d '[:space:]' || echo "")
  linear_epic=$(echo "$_status_block" | grep "LINEAR_EPIC:" | grep -v "LINEAR_EPIC_DONE\|LINEAR_EPIC_TOTAL" | tail -1 | sed 's/.*LINEAR_EPIC:[[:space:]]*//' | tr -d '[:space:]' || echo "")
  linear_epic_done=$(echo "$_status_block" | grep "LINEAR_EPIC_DONE:" | tail -1 | sed 's/.*LINEAR_EPIC_DONE:[[:space:]]*//' | tr -d '[:space:]' || echo "")
  linear_epic_total=$(echo "$_status_block" | grep "LINEAR_EPIC_TOTAL:" | tail -1 | sed 's/.*LINEAR_EPIC_TOTAL:[[:space:]]*//' | tr -d '[:space:]' || echo "")
else
  # No structured status block found — extract from full text
  exit_signal="false"
  status="UNKNOWN"
  tasks_done="0"
  files_modified_reported="0"
  work_type="UNKNOWN"
  recommendation=""
  linear_issue=""
  linear_url=""
  linear_epic=""
  linear_epic_done=""
  linear_epic_total=""
fi

# LINEAR-DASH: sanitize Linear fields; empty strings become JSON null
[[ "$linear_issue" =~ ^[Nn]one$ ]] && linear_issue=""
[[ "$linear_epic" =~ ^[Nn]one$ ]] && linear_epic=""
[[ "$linear_epic_done" =~ ^[0-9]+$ ]] || linear_epic_done=""
[[ "$linear_epic_total" =~ ^[0-9]+$ ]] || linear_epic_total=""

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
    count=$(echo "$count" | tr -cd '0-9')
    count=${count:-0}
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
  permission_denial_count=$(echo "$permission_denial_count" | tr -cd '0-9')
  permission_denial_count=${permission_denial_count:-0}
  [[ "$permission_denial_count" -gt 0 ]] && has_permission_denials="true"
fi

# Count actual files modified (from PostToolUse tracking)
actual_files_modified=0
if [[ -f "$RALPH_DIR/.files_modified_this_loop" ]]; then
  actual_files_modified=$(sort -u "$RALPH_DIR/.files_modified_this_loop" | wc -l | tr -cd '0-9')
  actual_files_modified=${actual_files_modified:-0}
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

# SESSION-SCOPE: Read the run ID written by ralph_loop.sh at startup. If
# status.json.ralph_run_id differs (stale file from a prior run that crashed
# before the startup reset completed), zero out session accumulators so this
# loop starts a clean session rather than inheriting stale totals.
_current_run_id=""
if [[ -f "$RALPH_DIR/.ralph_run_id" ]]; then
  read -r _current_run_id < "$RALPH_DIR/.ralph_run_id" 2>/dev/null || _current_run_id=""
fi
_status_run_id=""
if [[ -f "$RALPH_DIR/status.json" ]]; then
  _status_run_id=$(jq -r '.ralph_run_id // ""' "$RALPH_DIR/status.json" 2>/dev/null || echo "")
fi
_new_session="false"
if [[ -n "$_current_run_id" && "$_current_run_id" != "$_status_run_id" ]]; then
  _new_session="true"
fi

# PERF: Read loop count and write status.json in single operation (was: jq read + date + jq write = 3 subprocesses)
loop_count=0
prev_session_cost=0
prev_session_input=0
prev_session_output=0
if [[ -f "$RALPH_DIR/status.json" && "$_new_session" == "false" ]]; then
  loop_count=$(jq -r '.loop_count // 0' "$RALPH_DIR/status.json" 2>/dev/null || echo "0")
  prev_session_cost=$(jq -r '.session_cost_usd // 0' "$RALPH_DIR/status.json" 2>/dev/null || echo "0")
  prev_session_input=$(jq -r '.session_input_tokens // 0' "$RALPH_DIR/status.json" 2>/dev/null || echo "0")
  prev_session_output=$(jq -r '.session_output_tokens // 0' "$RALPH_DIR/status.json" 2>/dev/null || echo "0")
elif [[ -f "$RALPH_DIR/status.json" ]]; then
  # New session — inherit loop_count only; session accumulators start at 0
  loop_count=$(jq -r '.loop_count // 0' "$RALPH_DIR/status.json" 2>/dev/null || echo "0")
fi
# Validate loop_count is numeric before arithmetic
[[ "$loop_count" =~ ^[0-9]+$ ]] || loop_count=0
loop_count=$((loop_count + 1))
[[ "$prev_session_cost" =~ ^[0-9]+(\.[0-9]+)?$ ]] || prev_session_cost=0
[[ "$prev_session_input" =~ ^[0-9]+$ ]] || prev_session_input=0
[[ "$prev_session_output" =~ ^[0-9]+$ ]] || prev_session_output=0

# LINEAR-DASH: Best-effort extraction of token/cost usage from this loop.
# Claude Code supplies usage+cost in the stream result; try INPUT first, then transcript_path.
loop_input_tokens=0
loop_output_tokens=0
loop_cost_usd=0
_ti=$(echo "$INPUT" | jq -r '.usage.input_tokens // .message.usage.input_tokens // empty' 2>/dev/null || echo "")
_to=$(echo "$INPUT" | jq -r '.usage.output_tokens // .message.usage.output_tokens // empty' 2>/dev/null || echo "")
_tc=$(echo "$INPUT" | jq -r '.total_cost_usd // .message.total_cost_usd // empty' 2>/dev/null || echo "")
[[ "$_ti" =~ ^[0-9]+$ ]] && loop_input_tokens="$_ti"
[[ "$_to" =~ ^[0-9]+$ ]] && loop_output_tokens="$_to"
[[ "$_tc" =~ ^[0-9]+(\.[0-9]+)?$ ]] && loop_cost_usd="$_tc"

_transcript=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || echo "")

if [[ "$loop_cost_usd" == "0" ]]; then
  if [[ -n "$_transcript" && -f "$_transcript" ]]; then
    # Last "result" message in the transcript JSONL — agent mode emits one per turn.
    _result=$(tac "$_transcript" 2>/dev/null | grep -m 1 '"type":"result"' || true)
    if [[ -n "$_result" ]]; then
      _ti2=$(echo "$_result" | jq -r '.usage.input_tokens // empty' 2>/dev/null || echo "")
      _to2=$(echo "$_result" | jq -r '.usage.output_tokens // empty' 2>/dev/null || echo "")
      _tc2=$(echo "$_result" | jq -r '.total_cost_usd // empty' 2>/dev/null || echo "")
      [[ "$_ti2" =~ ^[0-9]+$ ]] && loop_input_tokens="$_ti2"
      [[ "$_to2" =~ ^[0-9]+$ ]] && loop_output_tokens="$_to2"
      [[ "$_tc2" =~ ^[0-9]+(\.[0-9]+)?$ ]] && loop_cost_usd="$_tc2"
    fi
  fi
fi

# Final fallback: scan the live `claude --output-format stream-json` output that ralph_loop.sh
# captures at $RALPH_DIR/logs/claude_output_<ts>.log. The official transcript at
# ~/.claude/projects/<proj>/<session>.jsonl does NOT contain `"type":"result"` lines — only
# the stream-json output does. The instance lock guarantees no concurrent loop, so the
# newest non-`_stream.log` file in logs/ is the current loop's stream. (At hook time
# ralph_loop has not yet overwritten it with the extracted result line.)
if [[ "$loop_cost_usd" == "0" ]]; then
  _live_stream=$(ls -t "$RALPH_DIR"/logs/claude_output_*.log 2>/dev/null \
                 | grep -v '_stream\.log$' | head -1 || true)
  if [[ -n "$_live_stream" && -f "$_live_stream" ]]; then
    _result=$(grep -E '"type"[[:space:]]*:[[:space:]]*"result"' "$_live_stream" 2>/dev/null | tail -1 || true)
    if [[ -n "$_result" ]]; then
      _ti3=$(echo "$_result" | jq -r '.usage.input_tokens // empty' 2>/dev/null || echo "")
      _to3=$(echo "$_result" | jq -r '.usage.output_tokens // empty' 2>/dev/null || echo "")
      _tc3=$(echo "$_result" | jq -r '.total_cost_usd // empty' 2>/dev/null || echo "")
      [[ "$_ti3" =~ ^[0-9]+$ ]] && loop_input_tokens="$_ti3"
      [[ "$_to3" =~ ^[0-9]+$ ]] && loop_output_tokens="$_to3"
      [[ "$_tc3" =~ ^[0-9]+(\.[0-9]+)?$ ]] && loop_cost_usd="$_tc3"
    fi
  fi
fi

# PHASE1: model used this loop (from last assistant message in transcript)
loop_model=""
loop_cache_read=0
loop_cache_create=0
if [[ -n "$_transcript" && -f "$_transcript" ]]; then
  _last_asst=$(tac "$_transcript" 2>/dev/null | grep -m 1 '"type":"assistant"' || true)
  if [[ -n "$_last_asst" ]]; then
    loop_model=$(echo "$_last_asst" | jq -r '.message.model // .model // empty' 2>/dev/null || echo "")
  fi
  # Sum cache read/create tokens across all assistant messages this loop
  _cr=$(grep '"type":"assistant"' "$_transcript" 2>/dev/null | jq -r '.message.usage.cache_read_input_tokens // 0' 2>/dev/null | awk '{s+=$1} END{print s+0}')
  _cc=$(grep '"type":"assistant"' "$_transcript" 2>/dev/null | jq -r '.message.usage.cache_creation_input_tokens // 0' 2>/dev/null | awk '{s+=$1} END{print s+0}')
  [[ "$_cr" =~ ^[0-9]+$ ]] && loop_cache_read="$_cr"
  [[ "$_cc" =~ ^[0-9]+$ ]] && loop_cache_create="$_cc"
fi

# PHASE1: sub-agent invocations this loop — count Task tool calls by subagent_type
# Single jq pass over the transcript (treated as JSONL via -s slurp) so counts sum correctly.
loop_subagents_json="{}"
if [[ -n "$_transcript" && -f "$_transcript" ]]; then
  loop_subagents_json=$(jq -cs '
    [ .[] | select(.type == "assistant") | .message.content[]?
      | select(.type == "tool_use" and .name == "Task")
      | .input.subagent_type // "unknown" ]
    | group_by(.) | map({(.[0]): length}) | add // {}
  ' "$_transcript" 2>/dev/null || echo "{}")
  [[ -z "$loop_subagents_json" || "$loop_subagents_json" == "null" ]] && loop_subagents_json="{}"
fi

# Merge session sub-agent counts (previous + this loop; zero base on new session)
prev_subagents_json="{}"
if [[ "$_new_session" == "false" && -f "$RALPH_DIR/status.json" ]]; then
  prev_subagents_json=$(jq -r '.session_subagents // {}' "$RALPH_DIR/status.json" 2>/dev/null || echo "{}")
fi
[[ -z "$prev_subagents_json" || "$prev_subagents_json" == "null" ]] && prev_subagents_json="{}"
session_subagents_json=$(jq -cn --argjson a "$prev_subagents_json" --argjson b "$loop_subagents_json" \
  '$a as $a | $b as $b | ($a | to_entries) + ($b | to_entries) | group_by(.key) | map({(.[0].key): (map(.value) | add)}) | add // {}' 2>/dev/null || echo "{}")
[[ -z "$session_subagents_json" || "$session_subagents_json" == "null" ]] && session_subagents_json="{}"

# TAP-588 (epic TAP-583): Count mcp__tapps-mcp__* and mcp__docs-mcp__* tool
# calls this loop. The transcript already lists every tool_use; one jq pass
# extracts MCP names, sums per-server, and emits a by-tool histogram. Without
# this counter we have no way to tell whether TAP-585's prompt guidance is
# actually moving Claude's behavior — see TAP-583 success criterion.
_empty_mcp_calls='{"tapps_mcp":0,"docs_mcp":0,"by_tool":{}}'
loop_mcp_calls_json="$_empty_mcp_calls"
if [[ -n "$_transcript" && -f "$_transcript" ]]; then
  loop_mcp_calls_json=$(jq -cs '
    [ .[] | select(.type == "assistant") | .message.content[]?
      | select(.type == "tool_use" and ((.name // "") | startswith("mcp__")))
      | .name ]
    | {
        tapps_mcp: (map(select(startswith("mcp__tapps-mcp__"))) | length),
        docs_mcp:  (map(select(startswith("mcp__docs-mcp__"))) | length),
        by_tool:   (group_by(.) | map({(.[0]): length}) | add // {})
      }
  ' "$_transcript" 2>/dev/null || echo "$_empty_mcp_calls")
  [[ -z "$loop_mcp_calls_json" || "$loop_mcp_calls_json" == "null" ]] && loop_mcp_calls_json="$_empty_mcp_calls"
fi

# Merge session MCP-call totals (previous + this loop; zero base on new session).
prev_mcp_calls_json="$_empty_mcp_calls"
if [[ "$_new_session" == "false" && -f "$RALPH_DIR/status.json" ]]; then
  prev_mcp_calls_json=$(jq -r '.session_mcp_calls // {"tapps_mcp":0,"docs_mcp":0,"by_tool":{}}' "$RALPH_DIR/status.json" 2>/dev/null || echo "$_empty_mcp_calls")
fi
[[ -z "$prev_mcp_calls_json" || "$prev_mcp_calls_json" == "null" ]] && prev_mcp_calls_json="$_empty_mcp_calls"
session_mcp_calls_json=$(jq -cn --argjson a "$prev_mcp_calls_json" --argjson b "$loop_mcp_calls_json" '
  {
    tapps_mcp: (($a.tapps_mcp // 0) + ($b.tapps_mcp // 0)),
    docs_mcp:  (($a.docs_mcp  // 0) + ($b.docs_mcp  // 0)),
    by_tool: (
      (($a.by_tool // {}) | to_entries) + (($b.by_tool // {}) | to_entries)
      | group_by(.key)
      | map({(.[0].key): (map(.value) | add)})
      | add // {}
    )
  }' 2>/dev/null || echo "$_empty_mcp_calls")
[[ -z "$session_mcp_calls_json" || "$session_mcp_calls_json" == "null" ]] && session_mcp_calls_json="$_empty_mcp_calls"

session_cost_usd=$(awk -v p="$prev_session_cost" -v l="$loop_cost_usd" 'BEGIN{printf "%.6f", p+l}')
session_input_tokens=$((prev_session_input + loop_input_tokens))
session_output_tokens=$((prev_session_output + loop_output_tokens))

# MERGE-1: also refresh calls_made_this_hour from .call_count, which ralph_loop.sh's
# update_status may have written as a stale pre-increment value at loop start.
fresh_calls_made=""
if [[ -f "$RALPH_DIR/.call_count" ]]; then
  read -r fresh_calls_made < "$RALPH_DIR/.call_count" 2>/dev/null || true
  [[ "$fresh_calls_made" =~ ^[0-9]+$ ]] || fresh_calls_made=""
fi

# Accumulate session cache stats (zero out on new session boundary)
prev_session_cache_read=0
prev_session_cache_create=0
if [[ "$_new_session" == "false" && -f "$RALPH_DIR/status.json" ]]; then
  prev_session_cache_read=$(jq -r '.session_cache_read_tokens // 0' "$RALPH_DIR/status.json" 2>/dev/null || echo "0")
  prev_session_cache_create=$(jq -r '.session_cache_create_tokens // 0' "$RALPH_DIR/status.json" 2>/dev/null || echo "0")
fi
[[ "$prev_session_cache_read" =~ ^[0-9]+$ ]] || prev_session_cache_read=0
[[ "$prev_session_cache_create" =~ ^[0-9]+$ ]] || prev_session_cache_create=0
session_cache_read=$((prev_session_cache_read + loop_cache_read))
session_cache_create=$((prev_session_cache_create + loop_cache_create))

# Sanitize all numeric/boolean fields for valid JSON output (MINGW/CRLF safety)
[[ "$question_count" =~ ^[0-9]+$ ]] || question_count=0
[[ "$permission_denial_count" =~ ^[0-9]+$ ]] || permission_denial_count=0
[[ "$files_modified" =~ ^[0-9]+$ ]] || files_modified=0
[[ "$asking_questions" == "true" ]] || asking_questions="false"
[[ "$has_permission_denials" == "true" ]] || has_permission_denials="false"

# Write status.json (atomic write via temp file).
# MERGE-1: Two writers (ralph_loop.sh update_status and this hook) emit different fields.
# Preserve the loop-writer fields (calls_made_this_hour, max_calls_per_hour, last_action,
# exit_reason, next_reset) so the monitor has a complete view regardless of which writer
# ran last.
local_tmp=$(mktemp "$RALPH_DIR/status.json.XXXXXX")
_now_ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Build this-hook's object, then merge onto the prior status.json so unrelated fields survive.
jq -n \
  --arg ts "$_now_ts" \
  --argjson lc "$loop_count" \
  --arg st "$status" \
  --arg es "$exit_signal" \
  --argjson td "$tasks_done" \
  --argjson fm "$files_modified" \
  --arg wt "$work_type" \
  --arg rec "$recommendation" \
  --argjson aq "$asking_questions" \
  --argjson qc "$question_count" \
  --argjson hpd "$has_permission_denials" \
  --argjson pdc "$permission_denial_count" \
  --arg li "$linear_issue" \
  --arg lu "$linear_url" \
  --arg le "$linear_epic" \
  --arg led "${linear_epic_done}" \
  --arg let "${linear_epic_total}" \
  --argjson lit "$loop_input_tokens" \
  --argjson lot "$loop_output_tokens" \
  --argjson lcu "$loop_cost_usd" \
  --argjson sit "$session_input_tokens" \
  --argjson sot "$session_output_tokens" \
  --argjson scu "$session_cost_usd" \
  --arg lm "$loop_model" \
  --argjson lcr "$loop_cache_read" \
  --argjson lcc "$loop_cache_create" \
  --argjson scr "$session_cache_read" \
  --argjson scc "$session_cache_create" \
  --argjson lsa "$loop_subagents_json" \
  --argjson ssa "$session_subagents_json" \
  --argjson lmc "$loop_mcp_calls_json" \
  --argjson smc "$session_mcp_calls_json" \
  --arg fcm "$fresh_calls_made" \
  --arg rid "$_current_run_id" \
  '{
    timestamp: $ts, loop_count: $lc, status: $st, exit_signal: $es,
    tasks_completed: $td, files_modified: $fm, work_type: $wt, recommendation: $rec,
    asking_questions: $aq, question_count: $qc,
    has_permission_denials: $hpd, permission_denial_count: $pdc,
    linear_issue: (if $li == "" then null else $li end),
    linear_url: (if $lu == "" then null else $lu end),
    linear_epic: (if $le == "" then null else $le end),
    linear_epic_done: (if $led == "" then null else ($led|tonumber) end),
    linear_epic_total: (if $let == "" then null else ($let|tonumber) end),
    loop_input_tokens: $lit, loop_output_tokens: $lot, loop_cost_usd: $lcu,
    session_input_tokens: $sit, session_output_tokens: $sot, session_cost_usd: $scu,
    loop_model: (if $lm == "" then null else $lm end),
    loop_cache_read_tokens: $lcr, loop_cache_create_tokens: $lcc,
    session_cache_read_tokens: $scr, session_cache_create_tokens: $scc,
    loop_subagents: $lsa, session_subagents: $ssa,
    loop_mcp_calls: $lmc, session_mcp_calls: $smc,
    ralph_run_id: (if $rid == "" then null else $rid end)
  }
  | if $fcm != "" then .calls_made_this_hour = ($fcm|tonumber) else . end
  ' > "$local_tmp.hook" 2>/dev/null

if [[ -f "$RALPH_DIR/status.json" ]] && jq -e 'type == "object"' "$RALPH_DIR/status.json" >/dev/null 2>&1; then
  # Merge: existing status.json + hook fields (hook wins on overlap).
  # LAST-ISSUE: after merge, if linear_issue is now non-null, persist it as
  # last_linear_issue so the monitor can show it during the next executing loop
  # (when linear_issue resets to null before Claude picks the next issue).
  jq -s '(.[0] * .[1]) | if .linear_issue != null then .last_linear_issue = .linear_issue else . end' \
    "$RALPH_DIR/status.json" "$local_tmp.hook" > "$local_tmp" 2>/dev/null \
    || cp "$local_tmp.hook" "$local_tmp"
else
  jq 'if .linear_issue != null then .last_linear_issue = .linear_issue else . end' \
    "$local_tmp.hook" > "$local_tmp" 2>/dev/null \
    || cp "$local_tmp.hook" "$local_tmp"
fi
rm -f "$local_tmp.hook" 2>/dev/null

mv "$local_tmp" "$RALPH_DIR/status.json"
rm -f "$local_tmp" 2>/dev/null  # WSL-1: catch cross-fs copy+unlink orphans

# PERF: Update circuit breaker in a single jq call (was: 2-3 separate jq + mktemp + mv per branch)
if [[ -f "$RALPH_DIR/.circuit_breaker_state" ]]; then
  # TAP-538: Guard against corrupt CB state. If jq cannot parse the file the
  # downstream `jq … > tmp && mv` pattern silently no-ops and the state stays
  # corrupt forever. Re-initialize to a safe skeleton and emit a WARN line so
  # the hook still exits 0 (preserving the loop) instead of crashing.
  if ! jq -e 'type == "object"' "$RALPH_DIR/.circuit_breaker_state" >/dev/null 2>&1; then
    echo "[$(date '+%H:%M:%S')] WARN: .circuit_breaker_state is corrupt — reinitializing to CLOSED" >&2
    printf '%s\n' '{"state":"CLOSED","consecutive_no_progress":0,"consecutive_permission_denials":0,"total_opens":0}' \
      > "$RALPH_DIR/.circuit_breaker_state"
  fi
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
  elif [[ "$exit_signal" == "true" && "$status" == "COMPLETE" ]]; then
    # EXIT-CLEAN: Claude reported clean completion (EXIT_SIGNAL: true + STATUS: COMPLETE)
    # but with 0 files modified and 0 tasks completed — this is the legitimate
    # "plan is empty / nothing to do" path, not stagnation. Reset the no-progress
    # counter so consecutive empty-plan loops don't trip the breaker on the
    # SAME signal Claude is already using to ask for clean shutdown.
    # Also reset permission denials (none happened) and ensure state stays CLOSED.
    jq '.consecutive_no_progress = 0 | .consecutive_permission_denials = 0 | .state = "CLOSED"' \
      "$RALPH_DIR/.circuit_breaker_state" > "$local_tmp" 2>/dev/null \
      && mv "$local_tmp" "$RALPH_DIR/.circuit_breaker_state"
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
