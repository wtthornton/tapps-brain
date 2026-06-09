# Archived Ralph scripts (retired 2026-06-09)

These scripts supported the Ralph autonomous Claude Code loop. tapps-brain now
uses **Linear + Cursor/Claude agents** for delivery (see EPIC-077 / TAP-3198).

| Script | Former purpose |
|--------|----------------|
| `run-ralph.sh` | Launch Ralph with Linear credential injection |
| `complete-ralph-deps.sh` | Install jq + Claude CLI for Ralph in WSL |
| `wsl-verify-ralph.sh` | Verify Ralph install in WSL |
| `wsl-fix-ralph-crlf.sh` | Fix CRLF on Ralph bash scripts |
| `wsl-run-ralph-bg.sh` | Detached tmux Ralph session |
| `Invoke-RalphWsl.ps1` / `Start-RalphWsl.ps1` | Windows → WSL Ralph wrappers |
| `analyze-ralph-startup.py` | Ralph startup timing analysis |
| `run-ralph-test.sh` | Ad-hoc Ralph test runner |

Do not run these for product work. Kept for reference only.
