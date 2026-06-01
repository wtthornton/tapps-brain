#!/bin/bash
# .ralph/hooks/protect-ralph-files.sh
# PreToolUse hook for Edit/Write. Blocks edits to .ralph/ except fix_plan.md,
# and blocks edits to the Claude Code control plane under .claude/.
# Exit 0 = allow, Exit 2 = block.

set -euo pipefail

# Resolve the project's .ralph/ to an absolute prefix. TAP-2344 (AgentForge
# 2026-05-22 F3): the previous unanchored `*/.ralph/*` glob blocked the
# global `~/.ralph/` install too, which forced agents to bypass the hook
# whenever they needed to hotfix the global library. Match against the
# project prefix only; anything outside the project (e.g. `~/.ralph/`) is
# allowed to fall through.
_proj_dir="${CLAUDE_PROJECT_DIR:-$PWD}"
RALPH_DIR="$_proj_dir/.ralph"
[[ -d "$RALPH_DIR" ]] || exit 0

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""')

# Normalize path (remove leading ./ if present)
FILE_PATH="${FILE_PATH#./}"

# Returns 0 if FILE_PATH points inside the project's .ralph/ directory.
# Accepts both absolute paths anchored at $RALPH_DIR and relative-shape
# paths the agent commonly emits (`.ralph/foo`).
_is_project_ralph() {
  local p="$1"
  case "$p" in
    "$RALPH_DIR"|"$RALPH_DIR"/*) return 0 ;;
    .ralph|.ralph/*) return 0 ;;
  esac
  return 1
}

if _is_project_ralph "$FILE_PATH"; then
  # Allow agent/coordinator-owned state files. The coordinator agent
  # (.claude/agents/ralph-coordinator.md MODE=brief) writes brief.json
  # and .linear_next_issue via the Claude Write tool, which fires this
  # PreToolUse hook. Pre-TAP-2471 the only allowed paths were fix_plan.md
  # and status.json — every coordinator write hit exit 2, silently masked
  # by the TAP-1875 retry-once + WARN-and-clear path. Evidence:
  # tapps-mcp/.ralph/.coordinator-brief.err captured Claude's own
  # thinking ("caught in a circular dependency"). Allowed paths:
  #   - fix_plan.md         — Ralph checks off tasks
  #   - status.json         — the hooks write this
  #   - brief.json          — coordinator MODE=brief step 3 (canonical brief)
  #   - .linear_next_issue  — coordinator MODE=brief step 4e (locality hint)
  #   - .last_completed_files — on-stop hook writes this; coordinator reads
  #   - .brief_cache/<id>.json — brief cache (parent harness writes via
  #     atomic_write, but the coordinator may also be asked to seed it)
  case "$FILE_PATH" in
    "$RALPH_DIR"/fix_plan.md|.ralph/fix_plan.md) exit 0 ;;
    "$RALPH_DIR"/status.json|.ralph/status.json) exit 0 ;;
    "$RALPH_DIR"/brief.json|.ralph/brief.json) exit 0 ;;
    "$RALPH_DIR"/.linear_next_issue|.ralph/.linear_next_issue) exit 0 ;;
    "$RALPH_DIR"/.last_completed_files|.ralph/.last_completed_files) exit 0 ;;
    "$RALPH_DIR"/.brief_cache/*|.ralph/.brief_cache/*) exit 0 ;;
    # TAP-2502: structured exit-signal sentinel — line 1 action enum,
    # line 2+ reason. on-file-change.sh validates + captures + deletes.
    # Narrow surface (single-shot write per loop) so the agent can't
    # use this to leak data; the file is removed by the hook after capture.
    "$RALPH_DIR"/.exit_signal_intent|.ralph/.exit_signal_intent) exit 0 ;;
  esac
  echo "BLOCKED: Cannot modify Ralph infrastructure file: $FILE_PATH" >&2
  echo "Allowed agent-writable paths under .ralph/: fix_plan.md, status.json, brief.json, .linear_next_issue, .last_completed_files, .brief_cache/, .exit_signal_intent." >&2
  exit 2
fi

# Block .ralphrc modifications. Two prior bugs fixed here:
#   1. The pattern `*".ralphrc"*` was substring-match, so it also matched
#      adjacent paths like `notmy.ralphrc.bak` or `vendor/foo.ralphrc-old`.
#      Anchor to a path boundary (`*/.ralphrc` or bare `.ralphrc`).
#   2. The `[[ -f "$FILE_PATH" ]]` guard let the Write tool *create* a new
#      `.ralphrc` (file did not yet exist → guard false → allow). The agent
#      could thus introduce a `.ralphrc` overriding model/auto-update/skill
#      settings even though existing `.ralphrc` edits were blocked.
# TAP-2344: anchor to the project root so a sibling project's .ralphrc
# isn't accidentally caught when the agent is editing across repos.
#
# .ralphrc.local is the operator-only override surface (gitignored, sourced
# by ralph_loop.sh after .ralphrc). It exists precisely so direct-to-main
# repos can persist RALPH_ALLOW_PUSH_MAIN=1 without the agent being able to
# self-unlock the R0 push-to-main block in validate-command.sh — so it must
# be blocked here too. Same anchoring rule: project-local only, never a
# sibling repo's file.
if [[ "$FILE_PATH" == "$_proj_dir/.ralphrc" ]]       || [[ "$FILE_PATH" == .ralphrc ]] || \
   [[ "$FILE_PATH" == "$_proj_dir/.ralphrc.local" ]] || [[ "$FILE_PATH" == .ralphrc.local ]]; then
  echo "BLOCKED: Cannot modify Ralph configuration: $FILE_PATH" >&2
  exit 2
fi

# Block the Claude Code control plane (TAP-623): settings, agents, hooks, commands.
# These files define Ralph's own guardrails; under bypassPermissions a single
# misread fix_plan.md line could otherwise disable every hook permanently.
if [[ "$FILE_PATH" == */.claude/settings*.json ]] || [[ "$FILE_PATH" == .claude/settings*.json ]] || \
   [[ "$FILE_PATH" == */.claude/agents/* ]]       || [[ "$FILE_PATH" == .claude/agents/* ]]       || \
   [[ "$FILE_PATH" == */.claude/hooks/* ]]        || [[ "$FILE_PATH" == .claude/hooks/* ]]        || \
   [[ "$FILE_PATH" == */.claude/commands/* ]]     || [[ "$FILE_PATH" == .claude/commands/* ]]; then
  echo "BLOCKED: Cannot modify Claude Code agent/hook config: $FILE_PATH" >&2
  exit 2
fi

exit 0
