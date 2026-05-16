# Ralph setup guide

This guide covers the end-to-end setup for running Ralph against the
tapps-brain project, including the Claude Agent Linear credential that
powers Ralph's exit-gate issue-count probe.

## Prerequisites

- `ralph` binary on `PATH` (installed globally or under `~/.local/bin`).
- `claude` CLI on `PATH` (`claude --version` works).
- Postgres running and `TAPPS_BRAIN_DATABASE_URL` set (see
  `docs/guides/postgres-dsn.md`).
- On Windows: run from WSL (see below).

## Quick start

```bash
cd ~/code/tapps-brain
export PATH="$HOME/.local/bin:$PATH"   # if ralph/claude live here
ralph                                  # basic loop
ralph --live                           # streaming output
ralph --monitor                        # tmux dashboard
```

## Credential load order

Ralph's Linear exit-gate probe (`LINEAR_API_KEY`) is NOT the same
credential as the interactive Claude Code plugin (OAuth, always authed as
Bill Thornton). It is a **Personal API key** generated while logged in as
the Claude Agent Linear user (`tapp.thornton+claude@gmail.com`).

### Load order (highest priority first)

1. **`LINEAR_API_KEY` already in environment** — Ralph uses it directly.
2. **`~/.config/claude-agent/linear.env`** — sourced by `scripts/run-ralph.sh`
   before exec'ing ralph. Key is scoped to the Ralph subshell only.
3. **Env file missing** — Ralph starts anyway; the exit-gate count probe
   is skipped with a warning (`Linear count unavailable — skipping exit gate`).

## Setting up the credential (one-time, human step)

The credential file must be created by a human operator logged into Linear as
Claude Agent. See `docs/guides/linear-claude-agent.md` for the full credential
lifecycle.

```bash
mkdir -p ~/.config/claude-agent
umask 077
printf 'LINEAR_API_KEY=lin_api_YOUR_KEY_HERE\n' > ~/.config/claude-agent/linear.env
chmod 600 ~/.config/claude-agent/linear.env
```

Verify the permissions:

```bash
ls -la ~/.config/claude-agent/linear.env
# expected: -rw------- 1 you you ...
```

## Running Ralph with the credential (recommended)

Use the provided wrapper instead of calling `ralph` directly. The wrapper
sources the credential file with an existence guard so Ralph starts
cleanly whether or not the key is present:

```bash
bash scripts/run-ralph.sh           # same as ralph
bash scripts/run-ralph.sh --live    # streaming output
bash scripts/run-ralph.sh --monitor # tmux dashboard
```

The wrapper prints a one-line notice on startup:

```
[run-ralph] Loaded Linear credentials from /home/you/.config/claude-agent/linear.env
```

or, if the file is missing:

```
[run-ralph] ~/.config/claude-agent/linear.env not found — LINEAR_API_KEY will be unset.
Ralph will run but the Linear exit-gate count probe will be skipped.
```

### Custom credential path

Override the default path with `CLAUDE_AGENT_LINEAR_ENV`:

```bash
CLAUDE_AGENT_LINEAR_ENV=/path/to/other.env bash scripts/run-ralph.sh
```

## Verifying the probe works

After creating the credential file and running Ralph via the wrapper,
check the loop startup output for:

```
Linear count (open_count) = N
```

If you still see `unavailable — skipping exit gate`, confirm:

1. `cat ~/.config/claude-agent/linear.env` shows `LINEAR_API_KEY=lin_api_...`
2. `chmod 600 ~/.config/claude-agent/linear.env` is set (world-readable
   files are silently ignored as a safety measure by some tooling).
3. You launched Ralph via `bash scripts/run-ralph.sh`, not `ralph` directly.

## Windows / WSL

From WSL, the wrapper works identically:

```bash
cd /mnt/c/cursor/tapps-brain  # or your repo path
bash scripts/run-ralph.sh --live
```

For a background session that survives after the WSL window closes, use
`scripts/wsl-run-ralph-bg.sh` (which wraps tmux). The credential file
path (`~/.config/claude-agent/linear.env`) refers to the WSL home, not
the Windows home — create the file inside WSL.

## Security notes

- `~/.config/claude-agent/linear.env` is outside the repo and therefore
  never committed (no `.gitignore` entry required).
- The wrapper does **not** `export` the key into the parent shell — it
  is scoped to the Ralph subshell only.
- Do not add `LINEAR_API_KEY` to `~/.bashrc` or any shell init file;
  that leaks the key into every interactive shell and child process.
