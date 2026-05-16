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
Claude Agent. See [`docs/guides/linear-claude-agent.md`](linear-claude-agent.md)
for the full credential lifecycle, the quick-verify smoke-test block, and
revocation/rotation guidance.

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

## Startup-duration metrics (TAP-1852)

The Ralph SessionStart hook writes one JSONL line per loop to
`.ralph/metrics/startup-YYYY-MM.jsonl` recording wall-clock timing for
each startup phase from the client's perspective:

| Field | What it measures |
|---|---|
| `mcp_probe_ms` | Pre-warm `curl /healthz` latency |
| `tools_list_ms` | `GET /v1/tools/list` static-snapshot latency |
| `session_start_ms` | `tapps_session_start` MCP tool latency (0 when not measured) |
| `linear_count_ms` | Linear count probe latency (0 when not measured) |
| `total_ms` | Total SessionStart hook wall-clock time |

Entries are appended one line at a time (`echo "..." >> file`); any
malformed line is silently skipped by the analyser.

### Analysing the metrics

```bash
# p50/p95/p99 for each phase over the last 7 days
python3 scripts/analyze-ralph-startup.py

# Custom look-back window
python3 scripts/analyze-ralph-startup.py --days 30

# Non-default metrics directory
python3 scripts/analyze-ralph-startup.py --metrics-dir /path/to/.ralph/metrics
```

Example output:

```
Ralph startup metrics — last 7 days  (42 samples)

Phase                      samples      p50      p95      p99      min      max
----------------------------------------------------------------------
pre-warm /healthz               42    312ms    890ms   1200ms     45ms   2100ms
/v1/tools/list                  42     38ms     82ms    120ms     12ms    200ms
tapps_session_start              0        —        —        —        —        —
Linear count probe               0        —        —        —        —        —
total hook duration             42    370ms    950ms   1380ms     60ms   2300ms
```

Phases showing `—` were not measured that run (0 in the JSONL).

## Timeout knobs

### `RALPH_MCP_PROBE_TIMEOUT_SECONDS` (`.ralphrc:266`)

Controls how long Ralph waits for `claude mcp list` to respond on startup.
Default: **120 s** — this is sufficient after the TAP-1832 cold-start fixes
(WS1.1–WS1.4: tools/list cache, deferred imports, docker healthcheck,
pre-warm hook) reduced the cold path to < 2 s.

If you still see `MCP probe failed: timed out` after those fixes are deployed,
check that the `tapps-brain-http` container is healthy first:

```bash
curl -f http://localhost:8080/healthz   # should return 200
docker compose ps tapps-brain-http      # should show "healthy"
```

Only bump the timeout as a last resort:

```bash
# In .ralphrc — human must apply; agent edits are blocked by TAP-623
sed -i 's/RALPH_MCP_PROBE_TIMEOUT_SECONDS=120/RALPH_MCP_PROBE_TIMEOUT_SECONDS=240/' .ralphrc
```

**Planned reversal:** The 240 s value is a short-term workaround only.
Once `tapps-brain-http` stays warm (via the docker healthcheck + pre-warm
hook from TAP-1835 / TAP-1837), revert to 120 s to surface probe regressions
sooner. TAP-1832 WS1.1–WS1.4 deliver the fixes that make 120 s reliable.

## Security notes

- `~/.config/claude-agent/linear.env` is outside the repo and therefore
  never committed (no `.gitignore` entry required).
- The wrapper does **not** `export` the key into the parent shell — it
  is scoped to the Ralph subshell only.
- Do not add `LINEAR_API_KEY` to `~/.bashrc` or any shell init file;
  that leaks the key into every interactive shell and child process.
