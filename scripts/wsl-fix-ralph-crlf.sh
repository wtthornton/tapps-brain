#!/usr/bin/env bash
# Run inside WSL: bash /mnt/c/.../scripts/wsl-fix-ralph-crlf.sh
set -euo pipefail
find "${HOME}/.ralph" -type f -name '*.sh' -exec sed -i 's/\r$//' {} +
find "${HOME}/.local/bin" -type f -name 'ralph*' -exec sed -i 's/\r$//' {} + 2>/dev/null || true
echo "CRLF fix done for ~/.ralph and ~/.local/bin/ralph*"
