#!/bin/bash
# .ralph/hooks/validate-command.sh
# PreToolUse hook for Bash commands.
# Reads command from stdin JSON, blocks destructive operations.
# Exit 0 = allow, Exit 2 = block.

set -euo pipefail

RALPH_DIR="${CLAUDE_PROJECT_DIR:-.}/.ralph"
[[ -d "$RALPH_DIR" ]] || exit 0

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

# Block destructive git commands
case "$COMMAND" in
  *"git clean"*|*"git rm"*|*"git reset --hard"*|*"git push --force"*|*"git push -f"*)
    echo "BLOCKED: Destructive git command not allowed: $COMMAND" >&2
    exit 2
    ;;
esac

# Block destructive file operations
case "$COMMAND" in
  *"rm -rf"*|*"rm -r "*|*"rm -fr"*)
    echo "BLOCKED: Recursive delete not allowed: $COMMAND" >&2
    exit 2
    ;;
esac

# Block modification of .ralph/ infrastructure via shell
if echo "$COMMAND" | grep -qE '(rm|mv|cp\s.*>|>)\s+(\./)?\.ralph/'; then
  echo "BLOCKED: Cannot modify .ralph/ infrastructure via shell: $COMMAND" >&2
  exit 2
fi

# Block modification of .claude/ config via shell
if echo "$COMMAND" | grep -qE '(rm|mv|cp\s.*>|>)\s+(\./)?\.claude/'; then
  echo "BLOCKED: Cannot modify .claude/ config via shell: $COMMAND" >&2
  exit 2
fi

exit 0
