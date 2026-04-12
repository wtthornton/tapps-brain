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

# Block destructive git commands (allow --force-with-lease which is a safer alternative)
if echo "$COMMAND" | grep -qE 'git push (--force|--force-if-includes|-f)(\s|$|")' 2>/dev/null && \
   ! echo "$COMMAND" | grep -q 'force-with-lease' 2>/dev/null; then
  echo "BLOCKED: Destructive git push not allowed: $COMMAND" >&2
  exit 2
fi
case "$COMMAND" in
  *"git clean"*|*"git rm"*|*"git reset --hard"*)
    echo "BLOCKED: Destructive git command not allowed: $COMMAND" >&2
    exit 2
    ;;
esac

# Block --no-verify flag (prevents skipping git hooks)
# Catches: git commit --no-verify, git push --no-verify, git --no-verify commit, etc.
if echo "$COMMAND" | grep -qE 'git\s+.*--no-verify' 2>/dev/null; then
  echo "BLOCKED: --no-verify not allowed (do not skip git hooks): $COMMAND" >&2
  exit 2
fi
case "$COMMAND" in
  *"git commit"*" -n "*|*"git commit"*" -n")
    echo "BLOCKED: git commit -n (--no-verify) not allowed: $COMMAND" >&2
    exit 2
    ;;
esac

# Block --no-gpg-sign flag (prevents skipping commit signing)
if echo "$COMMAND" | grep -qE 'git\s+(commit|merge|tag)\s.*--no-gpg-sign' 2>/dev/null; then
  echo "BLOCKED: --no-gpg-sign not allowed (do not skip commit signing): $COMMAND" >&2
  exit 2
fi

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
