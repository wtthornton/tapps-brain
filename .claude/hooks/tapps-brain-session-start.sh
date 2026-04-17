#!/usr/bin/env bash
# tapps-brain SessionStart hook
# Prompts Claude to prime the session by calling brain_recall with the
# opening topic, instead of waiting for the agent to remember.
#
# Reads hook JSON on stdin (ignored), prints a system-reminder to stdout.
# See docs/guides/claude-code-hooks.md for the full rationale.

INPUT=$(cat)
BRANCH=$(git -C "${CLAUDE_PROJECT_DIR:-.}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

echo "Before answering the user's first message, call brain_recall via the tapps-brain MCP server."
echo "Query: the user's opening topic (architecture, a specific module, a recent epic, or the current branch '${BRANCH}')."
echo "This primes cross-session memory. Skip the recall only if the user asked a trivial question (e.g. 'what time is it')."
exit 0
