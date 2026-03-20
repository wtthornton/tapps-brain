#!/usr/bin/env bash
# Upgrade Claude Code CLI inside WSL Ubuntu (run: bash scripts/wsl-upgrade-claude-code.sh)
set -euo pipefail

echo "Before: $(command -v claude 2>/dev/null || true) $(claude --version 2>/dev/null || echo '(claude not found)')"

# Prefer user-local install (no sudo); ensures newer claude wins if ~/.local/bin is first on PATH
export NPM_CONFIG_PREFIX="${HOME}/.local"
mkdir -p "${HOME}/.local/bin"

echo "Installing @anthropic-ai/claude-code to ${NPM_CONFIG_PREFIX} ..."
npm install -g @anthropic-ai/claude-code@latest

# Ensure wrapper is on PATH for non-login shells (e.g. Ralph / tmux)
if ! grep -q '.local/bin' "${HOME}/.bashrc" 2>/dev/null; then
  echo "" >> "${HOME}/.bashrc"
  echo '# Claude Code + tools (tapps-brain / Ralph)' >> "${HOME}/.bashrc"
  echo 'export PATH="${HOME}/.local/bin:${PATH}"' >> "${HOME}/.bashrc"
  echo "Appended PATH line to ~/.bashrc"
fi

export PATH="${HOME}/.local/bin:${PATH}"
echo "After:  $(command -v claude) $(claude --version)"

echo ""
echo "If \`claude --version\` is still old, /usr/local/bin/claude may shadow ~/.local/bin."
echo "Fix: export PATH=\"\$HOME/.local/bin:\$PATH\" (Ralph scripts already do this)."
