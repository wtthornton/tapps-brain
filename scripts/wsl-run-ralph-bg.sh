#!/usr/bin/env bash
# Start Ralph loop in background from WSL (log under project .ralph/logs/).
set -euo pipefail
export PATH="${HOME}/.local/bin:${PATH}"
REPO="${1:-/mnt/c/cursor/tapps-brain}"
cd "$REPO"
mkdir -p .ralph/logs
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG=".ralph/logs/nohup-ralph-${STAMP}.out"
: >>"$LOG"
nohup ralph >>"$LOG" 2>&1 &
echo "RALPH_PID=$!"
echo "RALPH_LOG=$LOG"
