# Claude Code hooks for tapps-brain

**Audience:** a human developer wiring Claude Code in a repo that talks to
the deployed `tapps-brain-http` container and wants the MCP session to
*auto-prime* itself instead of relying on the agent to remember to call
`brain_recall` / `brain_remember` on its own. Companion to
[mcp-client-repo-setup.md](mcp-client-repo-setup.md) (MCP transport wiring)
and [auto-recall.md](auto-recall.md) (in-process recall/capture API).

This guide only covers **Claude Code hooks** — `.claude/settings.json` +
`.claude/hooks/*.sh` entries that fire on SessionStart / Stop / PreCompact /
etc. It is the *client-side* lever for improving recall/capture discipline.
Server-side behaviour (decay, consolidation, diagnostics) is unchanged.

## Why hooks at all

The repo's `CLAUDE.md` already tells Claude *when* to call `brain_recall`
and `brain_remember` (see the "Cross-session memory" block). Those
behavioural rules work, but are advisory — a fresh Claude session can still
forget. Hooks are the harness's way of making a specific action happen at a
specific event, without trusting the agent to remember.

The only hook-integrated agent-memory system on the scorecard is
`claude-memory-compiler` (coleam00) — see
[memory-systems-scorecard.md](../research/memory-systems-scorecard.md)
for the full comparison. Its design — SessionStart / SessionEnd / PreCompact
hooks plus an LLM-driven compile pipeline — is the template this guide
selectively adopts.

## Recommendation

| Hook / pattern | Decision | Rationale |
|---|---|---|
| SessionStart → prompt-inject "call `brain_recall` now" | **Adopt** | Removes the "did Claude remember to recall" failure mode on turn 1. Zero external calls from the hook; the MCP session is already open. Matches the existing `tapps-session-start.sh` pattern for TappsMCP. |
| Stop / SessionEnd → server-side `memory_ingest` with the transcript | **Defer** | Tool exists (`memory_ingest`, `src/tapps_brain/mcp_server/__init__.py:1043`), but the hook would need to hold the bearer token and make a direct HTTP call with the transcript. The incremental value over the existing CLAUDE.md rules ("call `brain_remember` when a decision is made with rationale") isn't established. Revisit if the STORY-SC01 benchmark shows recall quality bottlenecked by missed captures. |
| PreCompact → back up scoring context | **Skip — already covered** | `.claude/hooks/tapps-pre-compact.sh` already runs for TappsMCP. Adding a tapps-brain PreCompact hook would duplicate the work; the SessionStart recall primer will re-inject relevant memories into the compacted context automatically. |
| LLM compile pipeline (`claude-memory-compiler`'s `compile.py`) | **Do not adopt** | Directly conflicts with ADR-007: "no LLM in the data path." Adopting it would require a new ADR, not a hook. |

## Adopted: SessionStart → `brain_recall` priming

### The hook script

`.claude/hooks/tapps-brain-session-start.sh`:

```bash
#!/usr/bin/env bash
# tapps-brain SessionStart hook
# Prompts Claude to prime the session by calling brain_recall with
# the opening topic, instead of waiting for the agent to remember.

INPUT=$(cat)
BRANCH=$(git -C "${CLAUDE_PROJECT_DIR:-.}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

echo "Before answering the user's first message, call brain_recall via the tapps-brain MCP server."
echo "Query: the user's opening topic (architecture, a specific module, a recent epic, or the current branch '${BRANCH}')."
echo "This primes cross-session memory. Skip the recall only if the user asked a trivial question (e.g. 'what time is it')."
exit 0
```

The script doesn't talk to the brain itself — it prints a system-reminder
that Claude reads on turn 1. The actual `brain_recall` call is made by
Claude via the MCP session, which is already open because `.mcp.json`
registered `tapps-brain` as a server.

### Register it in settings.json

Append a third entry to the existing `hooks.SessionStart` array in
`.claude/settings.json` (don't replace — TappsMCP and Ralph use earlier
entries):

```json
{
  "matcher": "startup|resume",
  "hooks": [
    {
      "type": "command",
      "command": ".claude/hooks/tapps-brain-session-start.sh"
    }
  ]
}
```

The `startup|resume` matcher means the hook fires on a fresh launch *and*
when a session is resumed (`claude --continue`). It does **not** fire on
`compact` (mid-session context compression) — the compact matcher is a
separate entry in the existing settings.

### Make the script executable

```bash
chmod +x .claude/hooks/tapps-brain-session-start.sh
```

### Cost

Each SessionStart adds ~4 lines to Claude's system-reminder buffer and one
extra `brain_recall` call — typically 50–200ms against the local
tapps-brain HTTP server. Negligible.

## Deferred: Stop → `memory_ingest`

### Why defer, not skip

The server-side tool exists —
`src/tapps_brain/mcp_server/__init__.py:1043` exposes `memory_ingest`,
which runs deterministic `extract_durable_facts` over arbitrary context
and writes new memories (skipping duplicates). The function is the same
one backing `MemoryStore.ingest_context`; no LLM is invoked. This is
exactly the "deterministic ingest on SessionEnd" path from the hook
recommendation.

What's *not* wired is the plumbing: a Stop (or SessionEnd) hook would need
to read the transcript from hook stdin, authenticate to the MCP server
with the bearer token from `.env`, and `POST` a `tools/call` to the
Streamable HTTP endpoint. Possible but non-trivial — see the TappsMCP
equivalent in `.claude/hooks/tapps-memory-auto-capture.sh` for the shape
of the stdin-parsing code.

### Sketch of the hook

If and when we wire this, the shape is:

```bash
#!/usr/bin/env bash
# tapps-brain Stop hook — deterministic memory_ingest of the transcript
# (No LLM call — the brain runs extract_durable_facts server-side.)

INPUT=$(cat)
PYBIN=$(command -v python3 || command -v python)
PROJECT_ID="<slug>"  # same as X-Project-Id in .mcp.json
AGENT_ID="claude-code-<user>"

# Extract a flat transcript string from the hook input JSON.
TRANSCRIPT=$(echo "$INPUT" | "$PYBIN" -c '
import sys, json
data = json.load(sys.stdin)
# Walk whatever shape Stop hooks receive; flatten to plain text.
print(json.dumps({"context": "...flattened transcript..."}))
')

# Source the bearer token (direnv has already loaded .env).
curl -sS -X POST \
  -H "Authorization: Bearer $TAPPS_BRAIN_AUTH_TOKEN" \
  -H "X-Project-Id: $PROJECT_ID" \
  -H "X-Agent-Id: $AGENT_ID" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  --data "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"memory_ingest\",\"arguments\":$TRANSCRIPT}}" \
  http://127.0.0.1:8080/mcp/ \
  >> "${CLAUDE_PROJECT_DIR:-.}/.tapps-brain/stop-ingest.log" 2>&1 || true

exit 0
```

### When to revisit

- If STORY-SC01's LoCoMo / LongMemEval run shows recall quality is
  bottlenecked by missed captures (i.e. relevant facts never made it into
  the store because the agent didn't call `brain_remember` during the
  turn).
- If we see `brain_recall` hit-rate drop in the flywheel / diagnostics
  (`diagnostics()` output) on sessions where a decision was clearly made
  but no memory was saved.

Until then, the CLAUDE.md rules + manual in-turn `brain_remember` calls
are the capture path.

## Skipped: PreCompact

The existing `.claude/hooks/tapps-pre-compact.sh` (for TappsMCP) already
backs up the compaction input to `.tapps-mcp/pre-compact-context.json`.
A tapps-brain PreCompact hook would either (a) duplicate that backup or
(b) do its own ingest — but ingest-on-compact has the same "capture
quality is bounded by CLAUDE.md discipline" issue as Stop, without the
upside of running at session boundaries.

More importantly, the SessionStart recall-priming hook already mitigates
the compaction-loss concern: when the compacted context lands, Claude
reads the updated memory section on the next turn and pulls relevant
memories back via `brain_recall`. The memory itself was never in the
compacted prompt — it's in the brain.

## Skipped: LLM compile pipeline

`claude-memory-compiler`'s design (see
[memory-systems-scorecard.md §claude-memory-compiler](../research/memory-systems-scorecard.md))
runs Claude Agent SDK extraction on SessionEnd (`flush.py`) and an
LLM-driven compile step (`compile.py`) that merges daily logs into
cross-referenced markdown concept / connection / Q&A articles. It is
entirely LLM-in-path.

That pattern is incompatible with the tapps-brain design stance codified
in ADR-007: deterministic merging, no LLM in the write path, text-
similarity consolidation. Adopting the compile pipeline would require
a new ADR re-opening that decision — not a hook change. See
[memory-systems-2026.md](../research/memory-systems-2026.md) §3 for the
trade-off analysis (retrieval-quality ceiling vs. deterministic cost and
throughput).

If LLM-assisted consolidation becomes desirable in the future, the
correct implementation path is:

1. New ADR weighing the cost/quality trade.
2. An optional `LLMConsolidator` Protocol under `_protocols.py` (like
   the existing `LLMJudge` for flywheel).
3. An opt-in flag on `MemoryProfile` — disabled by default, enabled per-
   project via profile YAML.

Hooks are the wrong layer for this change.

## Interaction with existing hooks

The repo's `.claude/hooks/` directory contains hooks for **three**
distinct systems:

- **Ralph** (`.ralph/hooks/on-*.sh`) — the autonomous development loop.
  Fires on SessionStart/Stop/PreToolUse/SubagentStop/TeammateIdle/
  TaskCompleted.
- **TappsMCP** (`.claude/hooks/tapps-*.sh`) — the quality tool.
  `tapps-session-start.sh` injects a reminder to call
  `tapps_session_start()`; `tapps-memory-auto-capture.sh` runs
  `tapps-mcp auto-capture` on Stop; `tapps-pre-compact.sh` backs up
  scoring context.
- **tapps-brain** (this guide) — the cross-session memory service.
  `tapps-brain-session-start.sh` is the only adopted hook.

All three coexist under the same `hooks.SessionStart` array in
`.claude/settings.json` — each entry is a separate matcher / command
pair. They run independently; Claude sees the concatenation of their
stdout as one system-reminder block on turn 1.

The three system-reminders visible at the start of every session are:

1. Ralph: "Injecting Ralph loop context…" (from the `.ralph` install).
2. TappsMCP: "REQUIRED: Call tapps_session_start() NOW as your first
   action."
3. tapps-brain (new): "Before answering the user's first message, call
   brain_recall…"

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Claude doesn't call `brain_recall` on turn 1 | Hook didn't fire, or stdout was empty | `chmod +x .claude/hooks/tapps-brain-session-start.sh`; confirm `.claude/settings.json` has the matcher entry; start a fresh session |
| `brain_recall` errors with `401 Unauthorized` | `direnv` didn't load `.env` for the shell that launched Claude Code | `cd` out/in; check `direnv status`; restart Claude Code from that shell |
| Hook runs but no memory section appears | `engagement_level: low` in the `repo-brain` profile, or the recall query is empty | See [auto-recall.md](auto-recall.md) "Engagement Levels"; set a sensible recall query in the hook output if the branch name is generic |
| Every session injects the same memories | Expected — `brain_recall` returns top-K by composite score. See the composite scoring formula in [retrieval.py](../../src/tapps_brain/retrieval.py) |

## References

- [mcp-client-repo-setup.md](mcp-client-repo-setup.md) — transport wiring
  prerequisite for any of this to work.
- [auto-recall.md](auto-recall.md) — in-process
  `RecallOrchestrator.recall() / .capture()` API used by tests and
  non-MCP integrations.
- [memory-systems-scorecard.md](../research/memory-systems-scorecard.md)
  — scoring of `claude-memory-compiler` and the hooks-vs-MCP rubric
  discussion (D6a parity check).
- [memory-systems-2026.md](../research/memory-systems-2026.md) §3 — the
  deterministic vs. LLM-in-path design trade.
- ADR-007 (see `docs/adr/`) — Postgres-only, deterministic write-path
  decision that rules out the `claude-memory-compiler` LLM compile
  pattern.
