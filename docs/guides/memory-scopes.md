# Memory scopes: project group vs Hive vs profile (GitHub #49)

Three concepts are easy to confuse. They are **separate** in tapps-brain.

| Concept | Where it lives | Purpose |
|--------|----------------|---------|
| **Project-local `memory_group`** | Column on each row in the project `memory.db` | Partition memories **inside one project** (e.g. `team-a`, `feature-x`). Optional; `NULL` / unset means *ungrouped* (still within the project). Filter with CLI `--group`, MCP `group`, or `memory_list_groups`. |
| **Hive `namespace`** | PostgreSQL Hive store (`TAPPS_BRAIN_HIVE_DSN`) | **Cross-agent** shared memory (`universal`, profile-aligned domains). Controlled via **`agent_scope`** on save (`private` / `domain` / `hive`) and Hive tools — **not** the same as `memory_group`. |
| **Profile tier / layer** | `MemoryProfile` | **Decay and classification** (half-lives, promotion). Describes *how* a memory ages, not a storage partition. |

## When to use which

- Use **`memory_group`** when one repo/project should hold **separate buckets** for retrieval (e.g. sub-teams or features) without publishing to Hive.
- Use **Hive `agent_scope` + namespace** when **multiple agents** need the **same** durable facts.
- Use **profile tiers** for **sensitivity and lifetime** (architectural vs ephemeral), not for “folders.”

## Anti-patterns

- Do **not** assume `memory_group` syncs to a Hive namespace (no implicit mapping).
- Do **not** rename Hive “namespace” to “group” in docs or mental model — reserved for project-local use here.

## Operators

- **CLI:** `tapps-brain store list --group X`, `store search Q --group X`, `store groups`, `tapps-brain recall MSG --group X`, `memory search Q --group X`.
- **MCP:** `memory_save` / `memory_search` / `memory_list` / `memory_recall` optional `group`; `memory_list_groups` lists distinct names.
- **Relay:** per-item optional `memory_group` or `group` in relay JSON (`relay_version` 1.0); see [Memory relay](memory-relay.md).

See also: `docs/planning/design-issue-49-multi-scope-memory.md`.
