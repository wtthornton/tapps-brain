# AgentForge Integration Guide

How any project connects to the running AgentForge stack — invoke agents, stream tasks, share Hive memory, and add project-specific agents.

---

## What Is AgentForge?

An AI agent orchestration server. It routes natural-language prompts to a catalog of Claude-powered specialist agents (security, testing, home automation, trading, etc.), auto-proposes new agents when no match exists, and persists cross-session memory via **tapps-brain**.

Other projects connect over HTTP. One server, many clients.

---

## Live Instances

| Instance | URL | Notes |
|----------|-----|-------|
| `agentforge-main` | http://localhost:8001 | Full dashboard + API |
| `agentforge-api` | http://localhost:8010 | API-only, no UI |
| `agentforge-project-a` | http://localhost:8002 | Isolated project instance |
| `agentforge-project-b` | http://localhost:8003 | Isolated project instance |

Swagger UI: http://localhost:8010/docs  
Health check: http://localhost:8001/health

---

## Calling AgentForge

### HTTP (synchronous)

```bash
curl -X POST http://localhost:8001/tasks/invoke \
  -H "Content-Type: application/json" \
  -d '{"prompt": "review this function for SQL injection risks"}'
```

```python
import httpx

result = httpx.post(
    "http://localhost:8001/tasks/invoke",
    json={"prompt": "what is the Bitcoin price?"},
).json()

print(result["output"])       # agent response
print(result["agent_used"])   # e.g. "crypto-price-tracker"
print(result["cost_usd"])     # e.g. 0.0031
```

**Target a specific agent** (skip routing):

```python
httpx.post(
    "http://localhost:8001/tasks/invoke",
    json={"prompt": "turn on living room lights", "config_hint": "home-assistant"},
)
```

### SSE streaming

```python
import httpx

with httpx.stream("POST", "http://localhost:8001/tasks/stream",
                  json={"prompt": "explain tapps-brain's decay model"}) as r:
    for line in r.iter_lines():
        if line.startswith("data:"):
            print(line[5:], end="", flush=True)
```

### WebSocket

```javascript
const ws = new WebSocket("ws://localhost:8001/ws/agent");
ws.onopen = () => ws.send(JSON.stringify({ prompt: "list failing CI checks" }));
ws.onmessage = ({ data }) => {
    const msg = JSON.parse(data);
    if (msg.type === "token") process.stdout.write(msg.content);
    if (msg.type === "done")  console.log("\n✓", msg.agent_used);
};
```

---

## Adding Project-Specific Agents

Create an `AGENT.md` file describing what the agent does. AgentForge hot-reloads it.

```markdown
---
name: my-project-deploy
description: Deploy my-project to staging or production
keywords: [deploy, release, my-project]
model: sonnet
allowed-tools: Bash(git:*, docker:*)
---

You are a deployment assistant for my-project located at ~/code/my-project.
Always confirm the environment before deploying to production.
```

**Install it:**

```bash
# Copy into the running container's custom-agents directory
docker cp my-project-deploy.md agentforge-main:/app/agents-custom/

# Or mount a directory in docker-compose.yml
volumes:
  - ~/code/my-project/.agentforge:/app/agents-custom/my-project:ro
```

**Verify:**

```bash
curl -s http://localhost:8001/agents | jq '.[].name' | grep my-project
```

---

## Shared Hive Memory

All AgentForge instances share a Postgres Hive (`docker-tapps-hive-db-1`). Any memory saved with `share=True` is visible to every agent across every instance.

### Connect AgentForge to the Hive

The Hive DB is on the `docker_default` network. AgentForge is on `agentforge_default`. Bridge them once:

```bash
docker network connect agentforge_default docker-tapps-hive-db-1
```

Then add to `~/code/AgentForge/.env`:

```env
TAPPS_HIVE_PASSWORD=tapps
AF_BRAIN_HIVE_DSN=postgres://tapps:tapps@docker-tapps-hive-db-1:5432/tapps_hive
AF_BRAIN_MIGRATION_MODE=per-agent
```

Restart: `docker compose -f ~/code/AgentForge/docker-compose.yml restart`

Verify: `curl -s http://localhost:8001/health | jq '.brain.hive'`

### Connect a custom project container to the Hive

```bash
# Add your container to the Hive's network
docker network connect docker_default my-project-container

# DSN to use inside your container:
# postgres://tapps:tapps@docker-tapps-hive-db-1:5432/tapps_hive
```

For full Hive setup, migration, and troubleshooting see
`docs/guides/hive-deployment.md` in this repo.

---

## Storing Agent Credentials

Credentials are encrypted at rest and injected into the agent subprocess at execution time — the LLM never sees them.

```bash
# Store
curl -X POST http://localhost:8001/secrets \
  -H "Content-Type: application/json" \
  -d '{"key_name": "MY_API_KEY", "value": "sk-...", "scope": "global"}'

# Reference in AGENT.md frontmatter:
# credentials:
#   - key: MY_API_KEY
#     scope: global
#     required: true
```

---

## Reading Brain Memory via API

```bash
# Facts known to a specific agent
curl -s http://localhost:8001/agents/home-assistant/memory | jq .

# All facts in the instance
curl -s http://localhost:8001/memory/facts | jq .

# Inject a fact directly
curl -X POST http://localhost:8001/memory/facts \
  -H "Content-Type: application/json" \
  -d '{"content": "my-project deploys to AWS us-east-1", "tier": "architectural"}'
```

---

## MCP Integration (Claude Code / Cursor)

```json
{
  "mcpServers": {
    "agentforge": {
      "url": "http://localhost:8001/mcp",
      "transport": "http"
    }
  }
}
```

Available MCP tools: `invoke_task`, `list_agents`, `get_agent`, `approve_agent`.

---

## Related Guides

| Guide | What it covers |
|-------|----------------|
| [hive-deployment.md](hive-deployment.md) | Full Hive Postgres setup, external networks, troubleshooting |
| [hive.md](hive.md) | tapps-brain Hive concepts and data flow |
| [agent-integration.md](agent-integration.md) | Using tapps-brain's `AgentBrain` class directly in code |
| [mcp.md](mcp.md) | tapps-brain MCP server reference |
