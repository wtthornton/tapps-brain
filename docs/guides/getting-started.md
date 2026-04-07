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

# Add everything (cli + mcp + faiss + reranker)
pip install tapps-brain[all]
```

> **Note:** The `[all]` extra does not include `[encryption]` or `[otel]` because they require platform-specific system libraries. Install those explicitly when needed.

---

## Vector search (built-in)

Semantic (embedding-based) search is built into the core install. The base `pip install tapps-brain` includes `sqlite-vec`, `sentence-transformers`, and `numpy` — no extra needed.

**Platform notes:**

- **sqlite-vec** requires a C compiler on some platforms if no pre-built wheel is available. On Ubuntu: `apt install build-essential`.
- **sentence-transformers** downloads model weights on first use (~90 MB for the default model). Ensure network access or pre-download for air-gapped environments.

**Optional FAISS:** For FAISS-based vector indexing (alternative to sqlite-vec), install the `[faiss]` extra: `pip install tapps-brain[faiss]`. For GPU-accelerated FAISS, install `faiss-gpu` separately from the [PyPI faiss-gpu package](https://pypi.org/project/faiss-gpu/) or via conda.

**Disabling vector search:** Set `TAPPS_SEMANTIC_SEARCH=0` to disable automatic embedding computation (e.g. for lightweight or test environments).

---

## Reranker configuration

The `[reranker]` extra adds cross-encoder reranking via the Cohere API to improve precision after BM25/hybrid retrieval:

```bash
pip install tapps-brain[reranker]
```

**Configuration** (via `InjectionConfig` or profile settings):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `reranker_enabled` | `True` | Enable reranking in the retrieval pipeline |
| `reranker_provider` | `"noop"` | Provider: `"noop"` (passthrough) or `"cohere"` |
| `reranker_api_key` | `None` | Cohere API key (also reads `COHERE_API_KEY` env) |
| `reranker_top_k` | `10` | Number of results to return after reranking |

**API key management:**

- Set `COHERE_API_KEY` in your environment or pass `reranker_api_key=` programmatically.
- Rotate keys by updating the environment variable; no restart is required for new `MemoryStore` instances.
- If the API key is missing or empty when `provider="cohere"`, the reranker silently falls back to noop (position-based scoring) and logs a debug message.

**Timeouts and errors:** The Cohere client uses its default HTTP timeout. If the API call fails (network error, rate limit, invalid key), the reranker falls back to the original BM25/hybrid ranking order and logs a warning (`reranker_failed_fallback_to_original`). No data is lost.

**Privacy note:** When `provider="cohere"`, memory entry values (text snippets) are sent to the Cohere API for reranking. Review your profile and compliance requirements before enabling cloud reranking in production.

---

## Next steps

- **Hive (multi-agent):** Share memories across agents → [Hive Guide](hive.md)
- **MCP setup:** Full client configuration → [MCP Server Guide](mcp.md)
- **OpenClaw plugin:** ContextEngine integration → [OpenClaw Guide](openclaw.md)
- **OpenClaw install/upgrade (operators):** Canonical steps → [OpenClaw runbook](openclaw-runbook.md)
- **Auto-recall:** Prompt injection and capture → [Auto-Recall Guide](auto-recall.md)
- **Federation:** Cross-project memory → [Federation Guide](federation.md)
