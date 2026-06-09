# Run Ralph via WSL. The global `ralph` file is `#!/bin/bash`; Windows cannot execute it
# natively — double-clicking it or running it from Explorer shows "Open with...".
#
# From repo root (PowerShell):
#   pwsh -File scripts/Invoke-RalphWsl.ps1 --status
#   pwsh -File scripts/Invoke-RalphWsl.ps1 --circuit-status
#   pwsh -File scripts/Invoke-RalphWsl.ps1 --live
#
# Requires: WSL, Ralph + claude + jq in Linux $HOME; see CLAUDE.md "Ralph on Windows".

$ErrorActionPreference = "Stop"
$repoWin = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
# Forward slashes avoid escape issues (e.g. \t in C:\cursor\tapps-brain).
$repoWinFwd = $repoWin -replace "\\", "/"
$wslpathOut = & wsl.exe wslpath -a $repoWinFwd 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "wslpath failed (is WSL installed?). Exit $LASTEXITCODE : $wslpathOut"
}
$repoUnix = ([string]$wslpathOut).Trim()
if ([string]::IsNullOrWhiteSpace($repoUnix)) {
    throw "wslpath returned empty for: $repoWinFwd"
}

# Bash-single-quote each argument for safe embedding in bash -lc
$quoted = foreach ($a in $args) {
    $escaped = $a -replace "'", "'\''"
    "'$escaped'"
}
$ralphArgs = if ($quoted.Count -gt 0) { $quoted -join " " } else { "" }

wsl.exe -- bash -lc "export PATH=\`$HOME/.local/bin:\`$PATH; cd '$repoUnix' && ralph $ralphArgs"
