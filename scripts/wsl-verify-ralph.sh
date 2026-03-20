#!/usr/bin/env bash
set -euo pipefail
export PATH="${HOME}/.local/bin:${PATH}"
echo "HOME=${HOME}"
if [[ -f "${HOME}/.ralph/ralph_loop.sh" ]]; then
  echo "OK: ralph_loop.sh exists"
else
  echo "FAIL: ralph_loop.sh missing"
  exit 1
fi
command -v jq >/dev/null && echo "OK: jq $(jq --version)" || echo "WARN: jq not found (install: sudo apt install -y jq)"
command -v ralph >/dev/null && echo "OK: ralph at $(command -v ralph)" || { echo "FAIL: ralph not in PATH"; exit 1; }
ralph --help 2>&1 | head -15
