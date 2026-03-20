#!/usr/bin/env bash
# Print path:line_number where ANTHROPIC_ appears (not the line content).
set -euo pipefail

report_matches() {
  local f="$1"
  if [[ ! -f "$f" ]]; then
    return 0
  fi
  while IFS= read -r line; do
    local num="${line%%:*}"
    printf '%s:%s\n' "$f" "$num"
  done < <(grep -n 'ANTHROPIC_' "$f" 2>/dev/null || true)
}

echo "=== home dotfiles ==="
report_matches "${HOME}/.bashrc"
report_matches "${HOME}/.profile"
report_matches "${HOME}/.bash_profile"
report_matches "${HOME}/.zshrc"
report_matches "${HOME}/.pam_environment"
report_matches "${HOME}/.bash_login"

echo "=== /etc/profile.d ==="
if [[ -d /etc/profile.d ]]; then
  for f in /etc/profile.d/*; do
    report_matches "$f"
  done
fi
report_matches /etc/environment

echo "=== ~/.claude *.json ==="
if [[ -d "${HOME}/.claude" ]]; then
  while IFS= read -r -d '' f; do
    report_matches "$f"
  done < <(find "${HOME}/.claude" -type f -name '*.json' -print0 2>/dev/null || true)
fi

echo "=== repo .env files (maxdepth 5) ==="
REPO="${1:-/mnt/c/cursor/tapps-brain}"
if [[ -d "$REPO" ]]; then
  while IFS= read -r -d '' f; do
    report_matches "$f"
  done < <(find "$REPO" -maxdepth 5 -type f \( -name '.env' -o -name '.env.*' \) -print0 2>/dev/null || true)
fi

echo "=== more system / config paths ==="
report_matches /etc/bash.bashrc
report_matches /etc/profile
report_matches "${HOME}/.config/fish/config.fish"
if [[ -d "${HOME}/.config/environment.d" ]]; then
  while IFS= read -r -d '' f; do
    report_matches "$f"
  done < <(find "${HOME}/.config/environment.d" -type f -print0 2>/dev/null || true)
fi

echo "=== env var names in this shell (no values) ==="
env | awk -F= '/^ANTHROPIC_/ {print "(active env) " $1}'
