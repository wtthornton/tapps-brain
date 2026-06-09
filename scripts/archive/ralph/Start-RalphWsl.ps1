# Start Ralph in WSL Ubuntu (background). From repo root:
#   pwsh -File scripts/Start-RalphWsl.ps1
# Tail log: wsl -d Ubuntu -- tail -f /mnt/c/cursor/tapps-brain/.ralph/logs/nohup-ralph-*.out
# Requires: Ralph + jq in Linux $HOME; see CLAUDE.md "Ralph on Windows (use WSL)".

wsl.exe -d Ubuntu -- bash -lc 'export PATH=$HOME/.local/bin:$PATH; cd /mnt/c/cursor/tapps-brain; bash scripts/wsl-run-ralph-bg.sh'
