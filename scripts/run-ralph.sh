#!/usr/bin/env bash
# run-ralph.sh — Ralph launcher with Claude Agent credential injection.
#
# Usage:
#   bash scripts/run-ralph.sh [ralph-args...]
#   bash scripts/run-ralph.sh --live
#   bash scripts/run-ralph.sh --monitor
#
# What this does:
#   1. Sources ~/.config/claude-agent/linear.env (if it exists) so that
#      LINEAR_API_KEY is available inside the Ralph subprocess.  The key is
#      intentionally *not* exported into the parent shell — it lives only for
#      the duration of the Ralph invocation.
#   2. Execs ralph with any arguments you pass, replacing this shell process.
#
# Why a wrapper instead of ~/.bashrc:
#   Sourcing the credential in ~/.bashrc leaks LINEAR_API_KEY into every
#   interactive shell and every child process on the machine.  This wrapper
#   scopes the key to the Ralph subshell only, matching the principle of
#   least privilege documented in docs/guides/linear-claude-agent.md.
#
# Credential file format (chmod 600, never committed):
#   LINEAR_API_KEY=lin_api_XXXX
#
# See docs/guides/ralph-setup.md for the full load-order and setup guide.

set -euo pipefail

CLAUDE_AGENT_ENV="${CLAUDE_AGENT_LINEAR_ENV:-${HOME}/.config/claude-agent/linear.env}"

# Source the credential file if it exists; no-op otherwise (graceful degradation).
if [[ -f "${CLAUDE_AGENT_ENV}" ]]; then
  # shellcheck source=/dev/null
  source "${CLAUDE_AGENT_ENV}"
  echo "[run-ralph] Loaded Linear credentials from ${CLAUDE_AGENT_ENV}"
else
  echo "[run-ralph] ${CLAUDE_AGENT_ENV} not found — LINEAR_API_KEY will be unset." \
       "Ralph will run but the Linear exit-gate count probe will be skipped."
fi

# Hand off to ralph, passing all arguments through.
exec ralph "$@"
