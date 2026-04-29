#!/bin/bash
# .ralph/hooks/protect-ralph-files.sh
# PreToolUse hook for Edit/Write. Blocks edits to .ralph/ except fix_plan.md,
# and blocks edits to the Claude Code control plane under .claude/.
# Exit 0 = allow, Exit 2 = block.

set -euo pipefail

RALPH_DIR="${CLAUDE_PROJECT_DIR:-.}/.ralph"
[[ -d "$RALPH_DIR" ]] || exit 0

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""')

# Normalize path (remove leading ./ if present)
FILE_PATH="${FILE_PATH#./}"

# Allow fix_plan.md edits (Ralph checks off tasks). Anchor to a slash so that
# "notralph/fix_plan.md" can't satisfy the *".ralph/fix_plan.md" suffix.
if [[ "$FILE_PATH" == */.ralph/fix_plan.md ]] || [[ "$FILE_PATH" == .ralph/fix_plan.md ]]; then
  exit 0
fi

# Allow status.json updates (hooks write this)
if [[ "$FILE_PATH" == */.ralph/status.json ]] || [[ "$FILE_PATH" == .ralph/status.json ]]; then
  exit 0
fi

# Block all other .ralph/ modifications
if [[ "$FILE_PATH" == */.ralph/* ]] || [[ "$FILE_PATH" == .ralph/* ]]; then
  echo "BLOCKED: Cannot modify Ralph infrastructure file: $FILE_PATH" >&2
  echo "Only .ralph/fix_plan.md checkboxes may be updated by the agent." >&2
  exit 2
fi

# Block .ralphrc modifications
if [[ "$FILE_PATH" == *".ralphrc"* ]] && [[ -f "$FILE_PATH" ]]; then
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
