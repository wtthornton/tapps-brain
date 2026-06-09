# Ralph Setup Guide (Windows + WSL)

Step-by-step guide for setting up Ralph on a new project. Covers the common pitfalls.

---

## Prerequisites (one-time, per machine)

### 1. Install Ralph in WSL

```bash
# In WSL terminal
git clone https://github.com/frankbria/ralph-claude-code.git /tmp/ralph-install
cd /tmp/ralph-install && bash install.sh
```

Verify: `ralph --help` should print usage info.

### 2. Install dependencies in WSL

```bash
sudo apt install -y jq tmux
```

### 3. Authenticate Claude Code with OAuth (Claude Max/Pro)

**This is the #1 setup issue.** Claude Code in WSL needs its own login — Windows credentials don't carry over.

```bash
# Check current auth
claude auth status
```

If it does NOT show `subscriptionType: "max"` (or "pro"), fix it:

```bash
# Remove any stale API key (overrides OAuth!)
grep -n "ANTHROPIC_API_KEY" ~/.bashrc ~/.profile ~/.zshrc 2>/dev/null

# If found, remove the line from that file, then:
unset ANTHROPIC_API_KEY

# Login with OAuth
claude login
# Select: 1 (Claude account with subscription)
# Complete browser OAuth flow
```

**Verify it worked:**

```bash
claude auth status
```

You should see:
```json
{
  "loggedIn": true,
  "authMethod": "claude.ai",
  "subscriptionType": "max"
}
```

If you see `"apiKeySource": "ANTHROPIC_API_KEY"` — there's still a key overriding OAuth. Find and remove it.

### 4. Verify everything

```bash
claude --version        # Should be >= 2.0.76
command -v ralph        # Should print a path
command -v jq           # Should print a path
claude auth status      # Should show subscriptionType: max/pro
```

---

## Per-project setup

### 1. Enable Ralph in your project

```bash
cd /mnt/c/cursor/your-project
ralph enable
```

This creates `.ralph/` directory with PROMPT.md, AGENT.md, fix_plan.md, and `.ralphrc`.

### 2. Edit the key files

| File | Purpose | Edit? |
|------|---------|-------|
| `.ralphrc` | Loop settings (rate limits, timeout, tools) | Tune as needed |
| `.ralph/PROMPT.md` | Process rules for the agent | Customize for your project |
| `.ralph/AGENT.md` | Build/test/lint commands | **Must match your project** |
| `.ralph/fix_plan.md` | Priority-ordered task list | **This is what Ralph works on** |

### 3. Run Ralph

```bash
cd /mnt/c/cursor/your-project
ralph --live
```

---

## Troubleshooting

### "Invalid API key" errors

```bash
# Check for stale API key in environment
env | grep ANTHROPIC

# If set, find the file and remove it
grep -rn "ANTHROPIC_API_KEY" ~/.bashrc ~/.profile ~/.zshrc /etc/environment 2>/dev/null

# Unset for current session
unset ANTHROPIC_API_KEY
```

### "Rate limit reached (5/5)"

The call counter persists across restarts. Reset it:

```bash
echo 0 > .ralph/.call_count
ralph --live
```

### Ralph won't stop on Ctrl+C

Ralph traps the first interrupt for cleanup. If it's stuck in a sleep countdown:

```bash
# From another terminal
pkill -f ralph_loop
```

### Claude Code "execution failed" on every loop

Check the output log for the real error:

```bash
ls -lt .ralph/logs/claude_output_*.log | head -1
# Then read the most recent one
cat .ralph/logs/<most-recent>.log
```

Common causes:
- Auth failure (see API key section above)
- Timeout too short (increase `CLAUDE_TIMEOUT_MINUTES` in `.ralphrc`)
- Permission denials (add tools to `ALLOWED_TOOLS` in `.ralphrc`)

### WSL can't open browser for OAuth

Claude will print a URL and say "Paste code here if prompted". Copy the URL, open it in your Windows browser, complete login, then paste the code back into the terminal.

---

## Running Ralph in background (survives WSL exit)

```bash
# Uses tmux so it persists after WSL window closes
cd /mnt/c/cursor/your-project
bash scripts/wsl-run-ralph-bg.sh

# Check on it later
tmux attach -t ralph-loop
```

---

## Quick-start checklist

- [ ] Ralph installed in WSL (`ralph --help` works)
- [ ] `jq` installed (`jq --version`)
- [ ] No `ANTHROPIC_API_KEY` in environment (`env | grep ANTHROPIC` returns nothing)
- [ ] `claude auth status` shows `subscriptionType: max` and `authMethod: claude.ai`
- [ ] Project has `.ralph/` and `.ralphrc` (`ralph enable`)
- [ ] `.ralph/fix_plan.md` has tasks
- [ ] `.ralph/AGENT.md` has correct build/test commands
- [ ] `ralph --live` starts and Claude executes successfully
