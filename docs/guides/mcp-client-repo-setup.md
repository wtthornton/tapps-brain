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
[`docs/guides/mcp-client-repo-setup.md`](docs/guides/mcp-client-repo-setup.md)
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
existing Ralph / TappsMCP hooks and does not require secrets in the hook
script (it uses the MCP session that `.mcp.json` already opens).

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

A healthy response is a JSON envelope with ~55 tool definitions.

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
