# Getting Started with tapps-brain

tapps-brain ships three first-class interfaces to the same memory engine. Choose the one that fits your workflow.

| Interface | Best for | Requires |
|-----------|----------|----------|
| **Python Library** | Custom agents, scripts, framework integrations | `pip install tapps-brain` |
| **CLI** | Manual memory management, shell scripts, quick inspection | same package — `tapps-brain` entry point |
| **MCP Server** | Claude Code, Cursor, VS Code Copilot, OpenClaw — zero-code AI editor integration | `pip install tapps-brain[mcp]` |

---

## Library — embed memory in your Python agent

```python
from pathlib import Path
from tapps_brain import MemoryStore

store = MemoryStore(Path("."))
store.save(key="db-choice", value="PostgreSQL — chosen for JSONB support", tier="architectural", source="human")
results = store.recall("database")
print(results.memory_section)   # formatted block ready for prompt injection
store.close()
```

Use this interface when you control the agent loop and want to call `recall()` / `save()` / `ingest_context()` directly.

---

## CLI — manage memory from the shell

```bash
tapps-brain save "db-choice" "PostgreSQL — chosen for JSONB support" --tier architectural
tapps-brain recall "database"
tapps-brain list --tier architectural
tapps-brain stats
```

Use this interface for ad-hoc inspection, scripted imports, or one-off memory operations without writing Python.

---

## MCP Server — plug into AI editors with zero code

Start the server and point your editor at it:

```bash
tapps-brain-mcp --project-dir /path/to/project
```

Add it to your editor's MCP config (e.g. `.mcp.json` for Claude Code):

```json
{
  "mcpServers": {
    "tapps-brain": {
      "command": "tapps-brain-mcp",
      "args": ["--project-dir", "/path/to/project"]
    }
  }
}
```

The AI assistant gets **54** MCP tools — core memory, sessions, Hive, federation, graph, tags, feedback, diagnostics, flywheel, OpenClaw migrate, and more — with no custom integration code required. See [MCP Server](mcp.md) for the full list.

---

## Choosing a profile

All three interfaces accept a `--profile` (CLI/MCP) or `profile_name=` (library) argument:

| Profile | Use case |
|---------|----------|
| `repo-brain` | Code repos, coding assistants *(default)* |
| `personal-assistant` | Personal AI assistants, daily notes |
| `customer-support` | Support agents, CRM |
| `research-knowledge` | Research and knowledge management |
| `project-management` | PM tools, sprint planning |
| `home-automation` | IoT and smart home agents |

See the [Profile Catalog](profile-catalog.md) for full details and the [Profile Design Guide](profiles.md) to build a custom profile.

---

## Next steps

- **Hive (multi-agent):** Share memories across agents → [Hive Guide](hive.md)
- **MCP setup:** Full client configuration → [MCP Server Guide](mcp.md)
- **OpenClaw plugin:** ContextEngine integration → [OpenClaw Guide](openclaw.md)
- **OpenClaw install/upgrade (operators):** Canonical steps → [OpenClaw runbook](openclaw-runbook.md)
- **Auto-recall:** Prompt injection and capture → [Auto-Recall Guide](auto-recall.md)
- **Federation:** Cross-project memory → [Federation Guide](federation.md)
