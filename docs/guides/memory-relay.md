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
| `memory_group` or `group` | string | no | Project-local partition (GitHub #49); same semantics as MCP `group` / CLI `--group`. Empty or whitespace → ungrouped. If both fields are set, `memory_group` wins. |

`relay_version` **1.0** is unchanged — consumers must ignore unknown item keys; older importers simply dropped these fields.

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

## Hive propagation and `memory_group`

When a relayed entry has `agent_scope` set to `domain` or `hive` (triggering Hive propagation), the entry's `memory_group` label is carried through to the Hive entry unchanged.  Subscribers that pull from the Hive and need project-local partition semantics can read the `memory_group` field on the returned row.  A value of `null`/absent means the entry was not assigned to any group (GitHub #51).

## See also

- [Memory scopes](memory-scopes.md) — `memory_group` vs Hive namespace vs profile layer.
- [Hive guide](hive.md) — `memory_group` field in the Hive schema.
- `memory_export` / `memory_import` MCP tools for full-store JSON (different shape).
- GitHub issue [#19](https://github.com/wtthornton/tapps-brain/issues/19).
