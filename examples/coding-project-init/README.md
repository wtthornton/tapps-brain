# Coding project init — connect your project to tapps-brain

This scaffold wires a new project to a **deployed** tapps-brain hub in two dimensions:

1. **Design/build-time** — your IDE's coding agent (Claude Code, Cursor, VS Code Copilot) talks to tapps-brain via MCP to save/recall memories as you code.
2. **Runtime** — your shipped app embeds `AgentBrain` to give its own agent loop persistent memory.

These are independent. You can wire either, both, or neither. The scaffold gives you a minimal working example of each so you can delete what you don't need.

---

## Prerequisites

A deployed tapps-brain hub reachable over MCP stdio (via Docker) or HTTP. If you haven't deployed one yet:

```bash
# from a tapps-brain checkout
docker compose -f docker/docker-compose.hive.yaml up -d --build
# → tapps-brain-http on :8080, visual dashboard on :8088, Postgres hive on :5433
```

---

## Files in this scaffold

| File | Purpose | Edit? |
|---|---|---|
| `.mcp.json.template` | MCP server entry for Claude Code / Cursor — spawns the deployed image. | Rename to `.mcp.json`, substitute `{{PROJECT_ID}}`. |
| `brain_init.py` | Runtime `AgentBrain` factory for your app's agent loop. | Import from your app code. Edit to match your agent_id/profile. |
| `.env.example` | Every env var the scaffold honors, with defaults. | Copy to `.env`, fill in. |
| `profile.yaml` | Per-project memory profile (layers, decay, ranking). | Edit for your domain; register once against the hub. |

---

## 1. Design-time — Claude Code / Cursor

Copy the MCP config template and substitute your project id:

```bash
cp .mcp.json.template .mcp.json
sed -i "s/{{PROJECT_ID}}/$(basename $(pwd))/g" .mcp.json
```

Open the project in Claude Code / Cursor. On session start, the `mcp__tapps-brain__*` tools appear. Ask the agent to save or recall — writes land in the deployed hive.

### One-time project registration (deployed / multi-tenant)

A deployed hub serves many projects, so it needs to know yours before it will accept writes (ADR-010 / [EPIC-069](../../docs/planning/epics/EPIC-069.md) — tracking the wire-protocol and CLI for this; until shipped, use in-process `AgentBrain` with `project_dir` for isolation):

```bash
# Once EPIC-069 lands:
tapps-brain project register <your-project-id> --profile ./profile.yaml
```

---

## 2. Runtime — embed AgentBrain in your app

`brain_init.py` exposes a `get_brain()` factory. Use it from your agent loop:

```python
from brain_init import get_brain

def run_task(user_input: str) -> str:
    with get_brain() as brain:
        context = brain.recall(user_input)
        # ... call your LLM with `context.memory_section` injected into the prompt ...
        brain.learn_from_success(f"Handled: {user_input[:60]}")
        return answer
```

The factory reads config from env vars (see `.env.example`). It's intentionally twenty lines — copy-paste it and edit to taste. Nothing here is a framework; it's the canonical example of the three calls you were going to write anyway.

---

## What this scaffold deliberately does not do

- **No framework adapter** (LangChain / LlamaIndex / …). Upstream APIs churn; embed `AgentBrain` directly in your integration.
- **No auto-save hooks.** Agents call `save()` / `learn_from_success()` explicitly. Automatic background saves hide the contract and make debugging harder.
- **No docker-compose for your project.** You already have one. Link your hub via env vars.

---

## Further reading

- [Agent integration guide](../../docs/guides/agent-integration.md) — full `AgentBrain` API surface.
- [MCP guide](../../docs/guides/mcp.md) — all MCP tools, project identity, auth.
- [Profiles](../../docs/guides/profiles.md) — how profile layers, decay, and ranking tune recall.
- [ADR-010](../../docs/planning/adr/ADR-010-multi-tenant-project-registration.md) — why `project_id` is on the wire instead of a filesystem `profile.yaml`.
