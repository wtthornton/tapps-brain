# Connecting a repo to the deployed tapps-brain via MCP

**Audience:** a human developer wiring Claude Code (or another MCP client)
in a local repo to talk to the deployed `tapps-brain-http` container.
**Server-side:** see [remote-mcp-integration.md](remote-mcp-integration.md).
This guide only covers the **client side**.

Every repo that connects should have its **own `project_id`**. Isolation is
enforced in Postgres via the composite `(project_id, agent_id)` key plus RLS
— sharing identities silently contaminates memory across projects.

## Prerequisites

- `tapps-brain-http` container running and healthy on this host
  (data-plane MCP on `http://127.0.0.1:8080/mcp/`).
- Bearer token from the host's `TAPPS_BRAIN_AUTH_TOKEN` (in AgentForge's
  `.env` in the current deployment).
- `direnv` installed once per host — see below.

## One-time host setup — direnv

`direnv` auto-materialises each repo's `.env` into the process env when
you `cd` into the directory. This lets every MCP client (Claude Code,
Cursor, VSCode Copilot, …) resolve `${VAR}` in its config from a single
`.env` file — no tool-specific secret duplication.

```bash
sudo apt-get install -y direnv
# add to ~/.bashrc (or ~/.zshrc for zsh)
echo 'eval "$(direnv hook bash)"' >> ~/.bashrc
exec bash   # pick up the hook in the current shell
```

## Per-repo setup (one-time, per repo)

### 1. Register the project on the brain

```bash
docker exec tapps-brain-http \
  tapps-brain project register <slug> \
    --profile /usr/local/lib/python3.13/site-packages/tapps_brain/profiles/repo-brain.yaml \
    --notes "<who/why>"
```

Slug must match `^[a-z0-9][a-z0-9_-]{0,63}$` — lowercase alnum + dash/underscore.
Usually just the repo's directory name.

Verify: `docker exec tapps-brain-http tapps-brain project list`.

### 2. Create `.env` with the bearer token

```bash
cat > .env <<EOF
# Consumed by .mcp.json via \${TAPPS_BRAIN_AUTH_TOKEN} substitution.
# Must match the token in the running tapps-brain-http container.
TAPPS_BRAIN_AUTH_TOKEN=<paste-token-here>
EOF
chmod 600 .env
```

### 3. Gitignore `.env`

Add `.env` to `.gitignore` **before** committing anything else.

### 4. Create `.envrc`

```bash
echo 'dotenv' > .envrc
direnv allow .
```

`cd` out and back in — `direnv` should report `+TAPPS_BRAIN_AUTH_TOKEN`.

### 5. Create `.mcp.json`

```json
{
  "mcpServers": {
    "tapps-brain": {
      "type": "http",
      "url": "http://127.0.0.1:8080/mcp/",
      "headers": {
        "Authorization": "Bearer ${TAPPS_BRAIN_AUTH_TOKEN}",
        "X-Project-Id": "<slug>",
        "X-Agent-Id": "claude-code-<user>"
      }
    }
  }
}
```

`.mcp.json` is safe to commit — it holds only the placeholder, not the
token. The trailing slash in `/mcp/` matters; `/mcp` responds with a 307
redirect.

### 5b. (Optional) Restrict tools with a profile

By default every client receives all **67** standard tools (`full` profile), all visible in `tools/list` on the Docker reference stack. Adding an `X-Brain-Profile` header cuts that down to the subset appropriate for the use case.

**Choosing a profile:**

| Use case | Profile | Tools (callable = eager) |
|---|---|---|
| AgentBrain consumer / brain_* facade only | `agent_brain` | 15 |
| Repo-embedded coding agent (Claude Code, Cursor, Aider) | `coder` | 21 |
| Read-only PR / code-review bot | `reviewer` | 9 |
| Bulk ingestion / seeding script | `seeder` | 6 |
| Human admin or operator console | `operator` | 80 |
| Everything (backwards-compatible default) | `full` | 67 |

Omitting the header is equivalent to `full` — no existing client breaks.

> **Deferred catalog (optional):** upstream bundles may mark tools with `defer_loading: true` to shrink the default `tools/list` payload to an 8-tool daily-driver set (TAP-1985). The **Docker reference stack disables defer_loading** so every registered tool appears in `tools/list`. Re-enable per-tool defer entries in `mcp_profiles.yaml` if you need the smaller catalog.

**Claude Code** — add `X-Brain-Profile` to `.mcp.json`:

```json
{
  "mcpServers": {
    "tapps-brain": {
      "type": "http",
      "url": "http://127.0.0.1:8080/mcp/",
      "headers": {
        "Authorization": "Bearer ${TAPPS_BRAIN_AUTH_TOKEN}",
        "X-Project-Id": "<slug>",
        "X-Agent-Id": "claude-code-<user>",
        "X-Brain-Profile": "coder"
      }
    }
  }
}
```

**Cursor** — same structure (Cursor reads `.mcp.json` from the project root):

```json
{
  "mcpServers": {
    "tapps-brain": {
      "type": "http",
      "url": "http://127.0.0.1:8080/mcp/",
      "headers": {
        "Authorization": "Bearer ${TAPPS_BRAIN_AUTH_TOKEN}",
        "X-Project-Id": "<slug>",
        "X-Agent-Id": "cursor-<user>",
        "X-Brain-Profile": "coder"
      }
    }
  }
}
```

**Aider** — pass headers via `.aider.conf.yml` `mcp_servers` block (Aider ≥ 0.60):

```yaml
# .aider.conf.yml
mcp_servers:
  tapps-brain:
    type: http
    url: http://127.0.0.1:8080/mcp/
    headers:
      Authorization: "Bearer ${TAPPS_BRAIN_AUTH_TOKEN}"
      X-Project-Id: "<slug>"
      X-Agent-Id: "aider-<user>"
      X-Brain-Profile: "coder"
```

**Verification** — after restarting your MCP client run `tools/list`; you
should see ~17 tools when using the `coder` profile, 10 when using
`agent_brain`, or 8 (the daily-driver eager budget) on the default `full`
profile. The healthcheck script (`scripts/brain-healthcheck.sh`) also
reports the configured profile when `mcpServers.tapps-brain` is present.

### Bridge-only brain-dev repos (this repository)

The **tapps-brain** source repo often wires **NLT MCP servers only** (no direct
`tapps-brain` entry in `.mcp.json` / `.cursor/mcp.json`) — memory goes through
BrainBridge. That is intentional.

| Check | Role |
|-------|------|
| `make brain-smoke-live` | Canonical **stack deploy** gate |
| `make brain-healthcheck` | Live MCP against `:8080/mcp/` via **server-mode** when IDE wiring is absent (warnings for missing `tapps-brain` block; FAIL only if the round-trip fails) |

Do **not** add a direct `tapps-brain` MCP block to this repo just to silence
healthcheck warnings — use server-mode, or wire consumers per the sections above.

#### Profile wire contract (stable across tapps-brain 3.x) — TAP-1579

For bridge implementations (e.g. tapps-mcp `BrainBridge`) that need to
distinguish "tool hidden by my profile" from "tool does not exist", these
elements are part of the 3.x stable surface:

| Surface | Value | Notes |
|---|---|---|
| Declaration | `X-Brain-Profile` HTTP header on every request | Set once in the client config (`.mcp.json` / `.aider.conf.yml`) — no per-call override needed. |
| Default when header is omitted | `full` (59 callable, 8 eager — TAP-1985) | Existing clients keep their callable surface; the eager `tools/list` payload shrinks to the daily-driver budget. |
| Default override | `TAPPS_BRAIN_DEFAULT_PROFILE` env var on the server | Operators may flip the default per deployment (see EPIC-073 rollout plan). |
| Out-of-profile `tools/call` error code | `-32602` (`INVALID_PARAMS`) | Distinct from `-32601` (`METHOD_NOT_FOUND`) so bridges can react. |
| Out-of-profile `tools/call` `error.data` | `{"reason": "out_of_profile", "tool": "<name>", "profile": "<name>", "suggested_profile": "<name>" \| null}` | Stable keys; consumers may dispatch on `reason`. `suggested_profile` (TAP-1972, v3.19.0+) names the smallest profile that exposes the denied tool — `null` when no profile exposes it. |
| Out-of-profile `tools/list` behavior | Tool is omitted from the response | No error; the tool simply isn't visible. |
| Unknown profile name | Fails open (acts like `full`) | Avoids denying legitimate operators against a server with stale YAML. |

A bridge that hits `-32602` with `data.reason == "out_of_profile"` should:
1. Retry with `X-Brain-Profile: <data.suggested_profile>` when that field is
   non-null (TAP-1972 self-routing), or
2. Surface the structured error to the caller so it can re-route.

A bridge that hits `-32601` should treat the tool as genuinely missing
(server has been upgraded / tool was deprecated) and adjust its call list,
not its profile.

### 6. Teach the MCP client how and when to use the brain

Wiring the MCP transport only opens the pipe — the client also needs
**behavioural rules** telling it *when* to call `brain_recall` and
`brain_remember`, what tier to save under, and what not to save. Without
these, a fresh session will ignore the brain.

For Claude Code, paste the following section into the repo's
`CLAUDE.md` (adjust `<slug>` and `<user>` to match your wiring). Other
MCP clients have equivalent places: Cursor → `.cursor/rules/*.mdc`,
VSCode Copilot → `.github/copilot-instructions.md`.

````markdown
## Cross-session memory (tapps-brain MCP)

This repo is wired to the deployed tapps-brain at
`http://127.0.0.1:8080/mcp/` as `project_id=<slug>`, agent
`claude-code-<user>`. See
[`docs/guides/mcp-client-repo-setup.md`](mcp-client-repo-setup.md)
for the wiring.

**Call `brain_recall` when:**
- Starting a session in this repo — recall with the topic the user
  opens with (architecture, recent work, a specific feature).
- The user asks "what did we decide about X", "why is Y the way it is",
  or "have we seen this before".
- You're about to make a non-trivial choice (a new pattern, a
  deviation from an existing approach) — recall first so prior
  decisions inform you.

**Call `brain_remember` when:**
- The user corrects your approach or teaches a non-obvious rule.
- A decision is made *with rationale* — the rationale is the
  memory-worthy part, not the decision itself.
- A debug session reveals a subtle invariant or a surprising
  constraint that isn't obvious from the code.

**Pick a tier (from the `repo-brain` profile):**
- `architectural` — system decisions, tech-stack choices, infra
  contracts. Half-life 180 days.
- `pattern` — coding conventions, API shapes, design patterns. 60d.
- `procedural` — workflows, build/deploy commands, runbooks. 30d.
- `context` — session-scope facts; use sparingly, decays in 14d.

Tag important entries with `critical` or `security` for ranking boost.

**Do NOT save:**
- Code patterns / file paths / module layout — derivable by reading
  the repo.
- Git history, recent diffs, who-changed-what — `git log` / `git blame`
  are authoritative.
- Ephemeral task state, current-conversation context, debug fix
  recipes — these belong in `TodoWrite` or the commit message.
- Anything with secrets, tokens, or PII.

**Split with the file-based auto-memory** at
`~/.claude/projects/.../memory/`:
- File auto-memory → **user** preferences + **feedback** on how to
  collaborate with this specific user. Lives across repos.
- tapps-brain MCP → **project** knowledge + **reference** pointers
  scoped to this repo. Shared across sessions and agents on this
  project. No manual sync between the two.
````

### 6b. (Optional) Wire a SessionStart hook to auto-prime recall

The CLAUDE.md rules above tell Claude *when* to call `brain_recall`, but a
fresh session can still forget on turn 1. If you want the harness to
guarantee the recall happens, add the SessionStart hook documented in
[claude-code-hooks.md](claude-code-hooks.md). It is additive to any
existing TappsMCP hooks and does not require secrets in the hook
script (it uses the MCP session that `.mcp.json` already opens).

### 6c. (Optional) Install the tapps-brain Claude Code skill

For agents working on **this repo**, the skill at
[`.claude/skills/tapps-brain/SKILL.md`](../../.claude/skills/tapps-brain/SKILL.md)
is auto-discovered by Claude Code — no action needed.

For agents working on **other repos** that consume the deployed brain,
install the skill once so the harness can trigger it on recall / remember
keywords. Three install paths:

```bash
# 0. HTTP-only consumers (AgentForge, CI) — fetch the version pinned to the
#    running brain (TAP-2981). No GitHub raw URL drift:
mkdir -p .claude/skills/tapps-brain
curl -fsSL http://127.0.0.1:8080/v1/skill | jq -r '.body' \
     > .claude/skills/tapps-brain/SKILL.md
# Response also includes {name, version} — compare version to your brain image tag.
```

```bash
# 1. Project-scoped (recommended for repo-specific behavioural rules) —
#    copy into the consumer repo's .claude/skills/ tree:
mkdir -p .claude/skills/tapps-brain
curl -fsSL https://raw.githubusercontent.com/wtthornton/tapps-brain/main/.claude/skills/tapps-brain/SKILL.md \
     -o .claude/skills/tapps-brain/SKILL.md
git add .claude/skills/tapps-brain/SKILL.md
```

```bash
# 2. User-scoped (one install, every repo for this user):
mkdir -p ~/.claude/skills/tapps-brain
curl -fsSL https://raw.githubusercontent.com/wtthornton/tapps-brain/main/.claude/skills/tapps-brain/SKILL.md \
     -o ~/.claude/skills/tapps-brain/SKILL.md
```

The skill is a **thin trigger** (~80 lines) — it points back at
[`llm-brain-guide.md`](llm-brain-guide.md), [`errors.md`](errors.md), and
the AgentForge integration guide rather than restating them. It is
semver-tagged (`version: "3.19.0"`) so consumers can check whether the
locally-installed skill matches the deployed brain version. Re-run the
curl command after a tapps-brain release to refresh.

### 7. Restart the MCP client

Launch Claude Code from a shell where `direnv` has loaded `.env` (i.e.
any shell `cd`'d into the repo after `direnv allow`). Confirm the server
appears by running `/mcp` inside the session.

## Verification

From a shell with `.env` sourced (direnv does this automatically):

```bash
curl -sSL -X POST \
  -H "Authorization: Bearer $TAPPS_BRAIN_AUTH_TOKEN" \
  -H "X-Project-Id: <slug>" \
  -H "X-Agent-Id: claude-code-<user>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  http://127.0.0.1:8080/mcp/
```

A healthy response is a JSON envelope with tool definitions. Expect 8 eager
tools on the default `full` profile (51 are deferred — see TAP-1985) or
~17 for `coder`.

## Knowledge-Graph tools (EPIC-076 + EPIC-074)

KG and experience tools on the `full`, `operator`, and `coder` profiles (several are `defer_loading: true` on `full` — still callable via `tools/call`):

| Tool | Purpose |
|---|---|
| `brain_record_event` | Write an `experience_events` row + optional memory / entity / edge / evidence in **one Postgres transaction**. |
| `brain_query_events` | **v3.24.0+** — query stored event payloads by `event_type`, time range, and optional `entity_id` (`payload.file_path` or `subject_key`). |
| `brain_record_events_batch` | N events in one MCP round-trip (per-event transactions; partial success allowed). |
| `brain_resolve_entity` | Resolve `(entity_type, canonical_name)` → stable entity UUID for edge specs. |
| `brain_get_neighbors` | Fetch 1-hop or 2-hop neighbourhood graph rows around one or more KG entities (structure only — not event payloads). |
| `brain_explain_connection` | Find the shortest path (≤ 3 hops) between two entities and return the full edge chain. |
| `brain_record_feedback` | Rate a KG edge as `edge_helpful` or `edge_misleading` to adjust its confidence score. |

### When to call each tool

**`brain_record_event`** — call after significant workflow steps (tool invocations, plan completions, approach failures) so the event and any new KG knowledge are durable in one round-trip. Example:

```python
brain_record_event(
    event_type="approach_failed",
    subject_key="my-approach-key",
    memory_key="auth-rewrite-failed",
    memory_value="JWTs with HS256 did not satisfy the compliance requirement.",
    memory_tier="architectural",
)
```

**`brain_get_neighbors`** — call when reasoning about how a concept relates to nearby entities. Combine with `brain_recall` for a richer context:

```python
brain_get_neighbors(
    entity_ids_json='["<entity-uuid>"]',
    hops=2,
    limit=20,
)
```

**`brain_explain_connection`** — call to trace *why* two entities are related before modifying their relationship:

```python
brain_explain_connection(subject_id="<uuid>", object_id="<uuid>")
# → { "found": true, "hops": 2, "path": [...] }
```

**`brain_record_feedback`** — call after verifying that a recalled edge was helpful or misleading:

```python
brain_record_feedback(
    edge_id="<edge-uuid>",
    feedback_type="edge_helpful",  # or "edge_misleading"
)
```

**`brain_query_events`** — call to read back metrics or audit events written via `brain_record_event` (v3.24.0+, TAP-3157). Example — quality scores for a file:

```python
brain_query_events(
    event_type="quality_metric",
    entity_id="src/tapps_brain/store.py",
    limit=50,
)
# → {"events": [{event_id, event_type, payload, ts, agent_id, ...}], "count": N}
```

Entity specs accept tapps-mcp shorthand: `{"type": "file", "id": "path/to/file.py"}` maps to `entity_type` / `canonical_name`.

### Matching HTTP endpoints

For non-MCP callers (HTTP REST clients, AgentForge):

| Endpoint | Body fields |
|---|---|
| `POST /v1/experience` | `event_type` (required) + optional `payload`, `entities`, `edges`, `evidence`, `memory_key/value` |
| `POST /v1/experience:query` | `event_type` (required) + optional `since`, `until`, `entity_id`, `limit` (cap 500) |
| `POST /v1/kg/neighbors` | `entity_ids` (list of UUID strings), `hops` (1–2), `limit` |
| `POST /v1/kg/explain` | `subject_id`, `object_id`, `max_hops` (1–3) |
| `POST /v1/kg/feedback` | `edge_id`, `feedback_type` (`edge_helpful` or `edge_misleading`) |

All endpoints require `X-Project-Id` and the same bearer token as the rest of the data plane.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Claude Code fails to parse `.mcp.json` with "env var not set" | `direnv` didn't load `.env` for the shell that launched Claude Code | `cd` out/in; check `direnv status`; restart Claude Code from that shell |
| `307` on `/mcp` | Missing trailing slash | Use `/mcp/` |
| `400 X-Project-Id header is required` | Header name mismatch (the old name was `X-Tapps-Project`) | Header is `X-Project-Id` on the HTTP adapter; `X-Tapps-Project` is MCP-only metadata |
| `ProjectNotRegisteredError` / 404 on first call | Brain is in strict mode (`TAPPS_BRAIN_STRICT=1`) and slug isn't registered | Run step 1 |
| Recall returns `[]` for memories you know exist | Wrong `project_id` — rows are filtered by tenant at Postgres RLS | Confirm `X-Project-Id` matches the slug you saved under |

## Rolling this out to another repo

The per-repo steps above are the checklist. Copy `.envrc` verbatim from
this repo; the only differences per repo are:

- The `<slug>` in `project register` and `X-Project-Id`.
- The token, if you've moved to per-tenant tokens (`tapps-brain project
  rotate-token <slug>` — currently the whole deployment shares one
  global token).
- `X-Agent-Id` suffix if a different user/agent identity is wanted.
- The CLAUDE.md rules block (step 6) — the template is generic; swap
  `<slug>` and `<user>` and decide whether the `repo-brain` profile fits.
  For non-code repos (PM, support, research), consider a different
  built-in profile and tier list.

## Installer-script outline

The 7 steps above are mechanical enough to wrap in a script. A future
`scripts/wire-repo-to-brain.sh <slug>` would:

1. `docker exec tapps-brain-http tapps-brain project register <slug> --profile <profile>`
2. Write `.env` with `TAPPS_BRAIN_AUTH_TOKEN=…` (pulled from the running
   container or a passed arg), `chmod 600`.
3. Ensure `.env` is in `.gitignore` (append if missing).
4. Write `.envrc` with `dotenv`, run `direnv allow .`.
5. Write `.mcp.json` with the HTTP transport block, substituting slug +
   user.
6. Append the "Cross-session memory" block to `CLAUDE.md` (idempotent —
   skip if already present).
7. Print instructions to restart the MCP client.

Not built yet — manual steps are the contract for now.
