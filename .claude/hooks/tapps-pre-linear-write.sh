#!/usr/bin/env bash
# tapps-mcp-hook-version: 3.10.9
# TappsMCP PreToolUse hook — Linear write gate (TAP-981)
# Blocks mcp__plugin_linear_linear__save_issue if no recent
# docs_validate_linear_issue sentinel (within 30 minutes). Bypass with
# TAPPS_LINEAR_SKIP_VALIDATE=1 (logged to .tapps-mcp/.bypass-log.jsonl).
#
# TAP-2012: this hook is *defense-in-depth* — the docs-mcp server enforces the
# generate→validate→save contract on the server side; this hook is a belt-and-
# suspenders shield against a misbehaving client that skipped the validator.
# Every allow/deny decision is logged to .tapps-mcp/hook-debug.log so operators
# debugging a blocked Linear call can tell whether the server already refused
# (structured envelope) or whether this hook is the only enforcement (bare
# exit 2).
ROOT_FOR_LOG="${CLAUDE_PROJECT_DIR:-$PWD}"
HOOK_DEBUG_LOG="$ROOT_FOR_LOG/.tapps-mcp/hook-debug.log"
_log() {
  # _log <decision> <reason> — append one JSONL line to the hook debug log.
  # decision ∈ {entry, allow, deny}; reason is a short tag.
  local decision="$1"
  local reason="$2"
  mkdir -p "$ROOT_FOR_LOG/.tapps-mcp" 2>/dev/null
  printf '{"ts":"%s","hook":"tapps-pre-linear-write","decision":"%s","reason":"%s","note":"[hook: defense-in-depth, primary: server-gate]"}\n' \
    "$(date -u +%FT%TZ)" "$decision" "$reason" >> "$HOOK_DEBUG_LOG" 2>/dev/null
}
_log entry invocation
INPUT=$(cat)
PYBIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
PARSED=$(echo "$INPUT" | "$PYBIN" -c   "import sys,json
try:
    d=json.load(sys.stdin)
    name=d.get('tool_name') or d.get('toolName') or ''
    inp=d.get('tool_input') or d.get('toolInput') or {}
    has_id=bool(inp.get('id'))
    has_template=bool(inp.get('title')) or bool(inp.get('description'))
    update_only='1' if (has_id and not has_template) else '0'
    print(name)
    print(update_only)
except Exception:
    print('')
    print('0')" 2>/dev/null)
TOOL=$(echo "$PARSED" | sed -n '1p')
UPDATE_ONLY=$(echo "$PARSED" | sed -n '2p')
case "$TOOL" in
  mcp__plugin_linear_linear__save_issue|save_issue) ;;
  *) _log allow not_linear_save_issue; exit 0 ;;
esac
# Update-only allow-list (TAP-981 FP reduction): save_issue calls that target
# an existing issue (id present) and do NOT modify title/description skip the
# sentinel — status, priority, label, assignee, parent updates don't need a
# fresh template validation.
if [ "$UPDATE_ONLY" = "1" ]; then
  _log allow update_only
  exit 0
fi
if [ "${TAPPS_LINEAR_SKIP_VALIDATE:-0}" = "1" ]; then
  ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
  mkdir -p "$ROOT/.tapps-mcp" 2>/dev/null
  echo "{\"ts\":\"$(date -u +%FT%TZ)\",\"bypass\":\"TAPPS_LINEAR_SKIP_VALIDATE\"}" \
    >> "$ROOT/.tapps-mcp/.bypass-log.jsonl" 2>/dev/null
  _log allow bypass_env
  exit 0
fi
ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
SENTINEL="$ROOT/.tapps-mcp/.linear-validate-sentinel"
if [ ! -f "$SENTINEL" ]; then
  cat >&2 <<'MSG'
TappsMCP: Blocked mcp__plugin_linear_linear__save_issue — no recent docs_validate_linear_issue call.
Route Linear writes through the `linear-issue` skill:
  1. docs_generate_story (or docs_generate_epic)
  2. docs_validate_linear_issue
  3. plugin save_issue
  4. tapps_linear_snapshot_invalidate
Or set TAPPS_LINEAR_SKIP_VALIDATE=1 for emergency bypass (logged).
See .claude/rules/linear-standards.md.
MSG
  _log deny missing_validate_sentinel
  exit 2
fi
NOW=$(date +%s)
SENT=$(cat "$SENTINEL" 2>/dev/null)
if ! echo "$SENT" | grep -Eq '^[0-9]+$'; then
  SENT=0
fi
AGE=$((NOW - SENT))
# Allow if validated within last 1800 seconds (30 minutes).
if [ "$AGE" -le 1800 ]; then
  _log allow fresh_sentinel
  exit 0
fi
cat >&2 <<MSG
TappsMCP: Blocked mcp__plugin_linear_linear__save_issue — last docs_validate_linear_issue was ${AGE}s ago (> 1800s freshness window).
Re-validate before push: docs_validate_linear_issue(title=..., description=..., ...)
Or set TAPPS_LINEAR_SKIP_VALIDATE=1 for emergency bypass (logged).
See .claude/rules/linear-standards.md.
MSG
_log deny stale_sentinel
exit 2
