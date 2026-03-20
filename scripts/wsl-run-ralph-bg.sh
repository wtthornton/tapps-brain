#!/usr/bin/env bash
# Start Ralph in a detached tmux session so it survives after `wsl.exe` exits.
# Plain nohup is killed when the WSL invocation from Windows ends.
set -euo pipefail
REPO="${1:-/mnt/c/cursor/tapps-brain}"
SESSION="${RALPH_TMUX_SESSION:-ralph-loop}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="${REPO}/.ralph/logs/tmux-ralph-${STAMP}.log"

mkdir -p "${REPO}/.ralph/logs"
: >>"$LOG"

if ! command -v tmux >/dev/null 2>&1; then
  echo "ERROR: tmux is required for background Ralph from Windows." >&2
  echo "Install: sudo apt install -y tmux" >&2
  exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux kill-session -t "$SESSION"
fi

# shellcheck disable=SC2016
tmux new-session -d -s "$SESSION" \
  "export PATH=\$HOME/.local/bin:\$PATH; cd $REPO; ralph 2>&1 | tee -a $LOG"

echo "RALPH_TMUX_SESSION=$SESSION"
echo "RALPH_LOG=$LOG"
echo "Attach: tmux attach -t $SESSION"
echo "List:   tmux ls"
