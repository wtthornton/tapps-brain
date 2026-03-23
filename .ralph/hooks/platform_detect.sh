#!/bin/bash
# .ralph/hooks/platform_detect.sh — Cross-platform environment detection (XPLAT-2)
# Source this from other hook scripts: source "${SCRIPT_DIR}/platform_detect.sh"

set -euo pipefail

# Detect the execution platform
ralph_detect_platform() {
    if [[ -f /proc/sys/fs/binfmt_misc/WSLInterop ]] || \
       grep -qi "microsoft" /proc/version 2>/dev/null; then
        echo "wsl"
    elif [[ "$(uname -s)" == "Darwin" ]]; then
        echo "macos"
    elif [[ "$(uname -s)" == "Linux" ]]; then
        echo "linux"
    else
        echo "unknown"
    fi
}

# Get the correct PowerShell command for this platform
ralph_get_powershell_cmd() {
    local platform
    platform=$(ralph_detect_platform)
    case "$platform" in
        wsl)
            # In WSL, Windows executables need .exe suffix
            if command -v powershell.exe &>/dev/null; then
                echo "powershell.exe"
            elif command -v pwsh &>/dev/null; then
                echo "pwsh"
            else
                echo ""
            fi
            ;;
        linux|macos)
            if command -v pwsh &>/dev/null; then
                echo "pwsh"
            else
                echo ""
            fi
            ;;
        *)
            if command -v powershell &>/dev/null; then
                echo "powershell"
            elif command -v pwsh &>/dev/null; then
                echo "pwsh"
            else
                echo ""
            fi
            ;;
    esac
}

# Get the correct Python command for this platform
ralph_get_python_cmd() {
    if command -v python3 &>/dev/null; then
        echo "python3"
    elif command -v python &>/dev/null; then
        echo "python"
    else
        echo ""
    fi
}
