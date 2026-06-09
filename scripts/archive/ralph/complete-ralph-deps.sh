#!/usr/bin/env bash
# Complete Ralph dependencies: jq + Claude CLI (run from WSL: bash scripts/complete-ralph-deps.sh)
set -euo pipefail

BIN_DIR="${HOME}/.local/bin"
mkdir -p "${BIN_DIR}"

# 1. Ensure jq is available (user-local, no sudo)
export PATH="${BIN_DIR}:${PATH}"
if [[ ! -x "${BIN_DIR}/jq" ]] && ! command -v jq &>/dev/null; then
  echo "Installing jq to ${BIN_DIR} ..."
  curl -sL https://github.com/jqlang/jq/releases/download/jq-1.8.1/jq-linux-amd64 -o "${BIN_DIR}/jq"
  chmod +x "${BIN_DIR}/jq"
  echo "  jq installed: $("${BIN_DIR}/jq" --version)"
else
  echo "jq OK: $(jq --version 2>/dev/null || "${BIN_DIR}/jq" --version)"
fi

# 2. Ensure ~/.local/bin is on PATH
export PATH="${BIN_DIR}:${PATH}"
if ! grep -q '.local/bin' "${HOME}/.bashrc" 2>/dev/null; then
  echo "" >> "${HOME}/.bashrc"
  echo '# Ralph / Claude Code / jq' >> "${HOME}/.bashrc"
  echo 'export PATH="${HOME}/.local/bin:${PATH}"' >> "${HOME}/.bashrc"
  echo "Appended PATH to ~/.bashrc"
fi

# 3. Upgrade Claude CLI
echo ""
echo "Upgrading Claude CLI ..."
export NPM_CONFIG_PREFIX="${HOME}/.local"
npm install -g @anthropic-ai/claude-code@latest

# 4. Verify
echo ""
echo "=== Ralph deps status ==="
echo "jq:    $(jq --version 2>/dev/null || echo 'NOT FOUND')"
echo "claude: $(claude --version 2>/dev/null || echo 'NOT FOUND')"
ralph-doctor 2>/dev/null || true
