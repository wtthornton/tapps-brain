#!/bin/bash
# .ralph/hooks/on-file-change.sh
# PostToolUse hook for Edit/Write. Tracks modified files per loop.
#
# TAP-2502: also captures the Write-tool sentinel `.ralph/.exit_signal_intent`
# — a structured replacement for the text-regex RALPH_STATUS path. When the
# agent writes that file, line 1 is an action enum (EMIT_EXIT_SIGNAL |
# CONTINUE_AND_RETRY | BLOCK | IMPLEMENT) and line 2+ is a free-form reason.
# Captured here as the durable 2026-industry-best signal path (zero regex
# ambiguity vs. the text fallback that TAP-2494 hardened).

set -euo pipefail

RALPH_DIR="${CLAUDE_PROJECT_DIR:-.}/.ralph"
[[ -d "$RALPH_DIR" ]] || exit 0

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""' 2>/dev/null || echo "")

if [[ -n "$FILE_PATH" ]]; then
  echo "$FILE_PATH" >> "$RALPH_DIR/.files_modified_this_loop"
fi

# TAP-2502: capture structured exit-signal sentinel.
# Match both absolute and repo-relative forms.
if [[ "$FILE_PATH" == */.ralph/.exit_signal_intent || "$FILE_PATH" == ".ralph/.exit_signal_intent" ]]; then
  _intent_file="$RALPH_DIR/.exit_signal_intent"
  if [[ -f "$_intent_file" ]]; then
    _action=$(sed -n '1p' "$_intent_file" 2>/dev/null | tr -d '[:space:]')
    _reason=$(sed -n '2,$p' "$_intent_file" 2>/dev/null | tr '\n' ' ' | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')

    # Validate against enum — invalid actions WARN and do nothing else.
    case "$_action" in
      EMIT_EXIT_SIGNAL|CONTINUE_AND_RETRY|BLOCK|IMPLEMENT)
        # Append JSONL log entry (audit trail).
        _ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
        _loop=$(jq -r '.loop_count // 0' "$RALPH_DIR/status.json" 2>/dev/null || echo 0)
        _jsonl_entry=$(jq -nc \
          --arg ts "$_ts" \
          --argjson loop "$_loop" \
          --arg action "$_action" \
          --arg reason "$_reason" \
          '{ts: $ts, loop: $loop, action: $action, reason: $reason}')
        printf '%s\n' "$_jsonl_entry" >> "$RALPH_DIR/.exit_signal_calls.jsonl"

        # On EMIT_EXIT_SIGNAL, append the current loop number to
        # .exit_signals.completion_indicators so the per-loop quorum check
        # at ralph_loop.sh sees it. Idempotent: if the loop number is already
        # the last entry, skip (handles double-write from tool retry).
        if [[ "$_action" == "EMIT_EXIT_SIGNAL" && -f "$RALPH_DIR/.exit_signals" ]]; then
          _tmp="$RALPH_DIR/.exit_signals.tmp.$$"
          jq --argjson loop "$_loop" '
            if (.completion_indicators[-1:] | .[0] // null) == $loop then .
            else .completion_indicators += [$loop] end
            | .completion_indicators = .completion_indicators[-5:]
          ' "$RALPH_DIR/.exit_signals" > "$_tmp" 2>/dev/null \
            && mv -f "$_tmp" "$RALPH_DIR/.exit_signals"
          rm -f "$_tmp" 2>/dev/null
        fi
        ;;
      *)
        echo "WARN: .exit_signal_intent contains invalid action: $_action (ignored)" >&2
        ;;
    esac

    # Delete the sentinel so the next write is unambiguous (no leftover state).
    rm -f "$_intent_file" 2>/dev/null
  fi
fi

exit 0
