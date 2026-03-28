# Memory relay (sub-agent → primary)

Cross-node setups: only the primary host runs tapps-brain. Sub-agents build a **relay** JSON envelope so the primary can bulk-import memories with:

```bash
tapps-brain relay import relay.json
# or
cat relay.json | tapps-brain relay import --stdin
```

MCP (on any node with the server): `tapps_brain_relay_export(source_agent, items_json)` returns a JSON object whose `payload` field is the relay document string.

## Schema `relay_version` **1.0**

Top-level object:

| Field | Type | Required | Description |
|--------|------|----------|-------------|
| `relay_version` | string | yes | Must be `"1.0"`. |
| `source_agent` | string | yes | Identifier for the sending agent (default `source_agent` for items). |
| `items` | array | yes | List of memory item objects. |

### Item object

| Field | Type | Required | Description |
|--------|------|----------|-------------|
| `key` | string | yes | Memory key (slug: `^[a-z0-9][a-z0-9._-]{0,127}$`). |
| `value` | string | yes | Memory body. |
| `tier` | string | no | Tier or profile layer name. Aliases: `long-term` → `architectural`, `short-term` / `short_term` → `pattern`, `identity` → `architectural`. Default `pattern`. |
| `scope` | string | no | **Legacy combined field:** if `private`, `domain`, or `hive` → sets **agent_scope** (visibility defaults to `project`). If `project`, `branch`, `session`, `shared`, or `ephemeral` → sets **memory scope** (`agent_scope` defaults to `private`). |
| `visibility` or `memory_scope` | string | no | Explicit memory visibility: `project`, `branch`, `session`, `shared`, `ephemeral`. |
| `agent_scope` | string | no | Hive propagation: `private`, `domain`, `hive`. |
| `tags` | string[] | no | Tags (store limits apply). |
| `source` | string | no | `human`, `agent`, `inferred`, `system` (default `agent`). |
| `source_agent` | string | no | Overrides envelope `source_agent` for this row. |
| `confidence` | number | no | Default `-1.0` (source default). |
| `branch` | string | no | Required when memory scope is `branch`. |

### Example

```json
{
  "relay_version": "1.0",
  "source_agent": "builder-agent",
  "items": [
    {
      "key": "poc-auth-decision",
      "value": "Use OAuth2 device flow for CLI.",
      "tier": "long-term",
      "scope": "hive",
      "tags": ["architecture", "auth"]
    }
  ]
}
```

## Import behaviour

- Invalid rows are **skipped** with a warning; valid rows are still saved.
- Bulk import uses rate-limit batch context `memory_relay`.
- RAG safety and write rules apply per row (blocked rows count as skipped).

## See also

- `memory_export` / `memory_import` MCP tools for full-store JSON (different shape).
- GitHub issue [#19](https://github.com/wtthornton/tapps-brain/issues/19).
