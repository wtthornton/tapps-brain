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

The AI assistant gets the full MCP tool surface — core memory, sessions, Hive, federation, graph, tags, feedback, diagnostics, flywheel, OpenClaw migrate, and more — with no custom integration code required. Canonical names/counts: [mcp-tools-manifest.json](../generated/mcp-tools-manifest.json). See [MCP Server](mcp.md) for setup.

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

## Minimal installation

The base package (`pip install tapps-brain`) includes only the core library with `pydantic`, `structlog`, and `pyyaml`. No CLI, no MCP server, no native extensions. This is the lightest install path for embedding tapps-brain as a library in your own Python agent:

```bash
pip install tapps-brain
```

Use the library API directly (`MemoryStore`, `recall()`, `save()`) without pulling in Typer, the MCP SDK, or any ML dependencies. Add extras incrementally as needed:

```bash
# Add CLI support
pip install tapps-brain[cli]

# Add MCP server
pip install tapps-brain[mcp]

# Add everything (cli + mcp + reranker)
pip install tapps-brain[all]
```

> **Note:** The `[all]` extra does not include `[encryption]` or `[otel]` because they require platform-specific system libraries. Install those explicitly when needed.

---

## Vector search (built-in)

Semantic (embedding-based) search is built into the core install. The base `pip install tapps-brain` includes `sentence-transformers` and `numpy` — no extra needed. Vectors are stored in PostgreSQL via the `pgvector` extension (HNSW index, cosine distance).

**Platform notes:**

- **sentence-transformers** downloads model weights on first use (~90 MB for the default model). Ensure network access or pre-download for air-gapped environments.
- **pgvector** must be installed in your Postgres cluster (`CREATE EXTENSION IF NOT EXISTS vector;`). The Docker image `pgvector/pgvector:pg17` includes it. See [`hive-deployment.md`](hive-deployment.md) for setup.


---

## Reranker configuration

The `[reranker]` extra adds local cross-encoder reranking via FlashRank to improve precision after BM25/hybrid retrieval:

```bash
pip install tapps-brain[reranker]
```

FlashRank runs entirely on-device (CPU, ~4MB model). No API key needed. No data leaves the machine.

**Configuration** (via `InjectionConfig` or profile settings):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `reranker_enabled` | `True` | Enable reranking in the retrieval pipeline |
| `reranker_top_k` | `10` | Number of results to return after reranking |

When `reranker_enabled=True` and flashrank is installed, reranking is automatic. If flashrank is not installed, the reranker falls back to noop (position-based scoring).

**Errors:** If the FlashRank model fails at runtime, the reranker falls back to the original BM25/hybrid ranking order and logs a warning. No data is lost.

---

## Next steps

- **Hive (multi-agent):** Share memories across agents → [Hive Guide](hive.md)
- **MCP setup:** Full client configuration → [MCP Server Guide](mcp.md)
- **OpenClaw plugin:** ContextEngine integration → [OpenClaw Guide](openclaw.md)
- **OpenClaw install/upgrade (operators):** Canonical steps → [OpenClaw runbook](openclaw-runbook.md)
- **Auto-recall:** Prompt injection and capture → [Auto-Recall Guide](auto-recall.md)
- **Federation:** Cross-project memory → [Federation Guide](federation.md)
