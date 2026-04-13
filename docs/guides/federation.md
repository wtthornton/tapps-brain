# Federation Guide

Cross-project memory sharing via a central hub store.

> **v3 (current):** The Federation backend is **PostgreSQL-only** (ADR-007). Set
> `TAPPS_BRAIN_FEDERATION_DSN=postgres://…` to activate it. SQLite Federation
> support was removed in v3. Sections below that reference `federated.db` or
> `FederatedStore()` describe v2 behaviour; the hub-and-spoke concepts are
> unchanged. See [postgres-dsn.md](postgres-dsn.md) for v3 env-var reference.

**Story:** STORY-002.5 from EPIC-002

**Related:** Hive (cross-**agent** sharing) is different from federation — see **[Hive vs federation](hive-vs-federation.md)**.

## Overview

Federation uses a **hub-and-spoke model**. Each project is a spoke that
explicitly publishes shared-scope memories to a central PostgreSQL hub. Projects
subscribe to receive memories from other projects. No data is shared
automatically.

```
Project A ──publish──> Hub (PostgreSQL) <──publish── Project B
Project A <──sync────> Hub (PostgreSQL) <──sync────> Project B
```

Key properties:

- **Explicit-only sharing** -- memories must be marked `scope="shared"` and
  explicitly synced.
- **Local-wins conflict resolution** -- if a key already exists locally, the
  hub version is never imported.
- **No auto-sharing** -- nothing leaves a project unless you call `sync_to_hub`.

## Registration

Register a project to make it visible in the federation hub:

```python
from tapps_brain.federation import register_project, unregister_project

# Register with optional tags
config = register_project(
    project_id="my-api",
    project_root="/home/user/my-api",
    tags=["python", "web"],
)

# Re-registering updates root/tags (idempotent)
config = register_project("my-api", "/home/user/my-api-v2", tags=["python"])

# Unregister removes the project and its subscriptions
config = unregister_project("my-api")
```

The hub supports up to 50 registered projects.

## Publishing

Use `FederatedStore.publish()` to push memory entries to the hub, or
`sync_to_hub()` for the common case of syncing all shared-scope entries
from a `MemoryStore`.

### Direct publish

```python
from tapps_brain.federation import FederatedStore

hub = FederatedStore()  # uses default ~/.tapps-brain/memory/federated.db
count = hub.publish(
    project_id="my-api",
    entries=shared_entries,      # list[MemoryEntry]
    project_root="/home/user/my-api",
)
print(f"Published {count} entries")

# Remove specific entries
hub.unpublish("my-api", keys=["obsolete-pattern"])

# Remove all entries for a project
hub.unpublish("my-api")
```

### sync_to_hub

```python
from tapps_brain.federation import FederatedStore, sync_to_hub
from tapps_brain.store import MemoryStore

store = MemoryStore("/home/user/my-api")
hub = FederatedStore()

# Publish all shared-scope entries
result = sync_to_hub(store, hub, project_id="my-api", project_root="/home/user/my-api")
# result: {"published": 5, "skipped": 0}

# Publish specific keys only
result = sync_to_hub(store, hub, project_id="my-api", keys=["api-pattern", "db-pattern"])
```

Only entries with `scope="shared"` are published. Project-scoped and
branch-scoped entries are never sent to the hub.

### `memory_group` on the hub

Shared entries may carry an optional project-local **`memory_group`** (same field as in the project DB). **Publish** copies it to the hub row; **sync from hub** restores it on imported memories so subscribers keep the publisher’s partition label. **`FederatedStore.search(..., memory_group="…")`** restricts hub results to that label. Schema: `docs/engineering/data-stores-and-schema.md` (federation hub).

## Subscribing

Create a subscription to control which memories a project pulls from the hub:

```python
from tapps_brain.federation import add_subscription, register_project

register_project("my-api", "/home/user/my-api")
register_project("shared-lib", "/home/user/shared-lib")

# Subscribe to specific sources
config = add_subscription(
    subscriber="my-api",
    sources=["shared-lib"],
    tag_filter=["api", "core"],   # only import entries with these tags
    min_confidence=0.7,           # skip low-confidence entries
)

# Subscribe to all projects (sources=None or omitted)
config = add_subscription(subscriber="my-api")
```

`FederationSubscription` fields:

| Field            | Default | Description                                      |
|------------------|---------|--------------------------------------------------|
| `subscriber`     | --      | Project ID of the subscribing project            |
| `sources`        | `[]`    | Source project IDs (empty = all projects)        |
| `tag_filter`     | `[]`    | Only import entries matching at least one tag    |
| `min_confidence` | `0.5`   | Skip entries below this confidence threshold     |

Adding a subscription replaces any existing subscription for that subscriber.
The hub supports up to 50 subscriptions.

## Syncing

Pull subscribed memories from the hub into the local store:

```python
from tapps_brain.federation import FederatedStore, sync_from_hub, load_federation_config
from tapps_brain.store import MemoryStore

store = MemoryStore("/home/user/my-api")
hub = FederatedStore()
config = load_federation_config()

result = sync_from_hub(store, hub, project_id="my-api", config=config)
# result: {"imported": 3, "skipped": 1, "conflicts": 1}
```

During sync:

- **Confidence filter** -- entries below the subscription's `min_confidence`
  are skipped.
- **Tag filter** -- entries must have at least one tag matching the
  subscription's `tag_filter` (if set).
- **Local-wins** -- if the key already exists in the local store, the hub
  entry is skipped and counted as a conflict.
- **Provenance tags** -- imported entries are tagged with `"federated"` and
  `"from:<source_project_id>"`, and their `source_agent` is set to
  `"federated:<source_project_id>"`.

## Federated Search

Search across both local and hub stores in a single call:

```python
from tapps_brain.federation import FederatedStore, federated_search
from tapps_brain.store import MemoryStore

store = MemoryStore("/home/user/my-api")
hub = FederatedStore()

results = federated_search(
    query="authentication pattern",
    local_store=store,
    federated_store=hub,
    project_id="my-api",
    max_results=20,
)

for r in results:
    print(f"[{r.source}] {r.key}: {r.value} (score={r.relevance_score:.2f})")
```

Behavior:

- **Local boost** -- local results get a 1.2x relevance score multiplier.
- **Deduplication** -- if the same key exists locally and in the hub, only
  the local version appears.
- **Sorting** -- results are sorted by relevance score descending.
- Use `include_local=False` or `include_hub=False` to search only one store.

## Configuration

Federation state is stored in `~/.tapps-brain/memory/federation.yaml`.

```python
from tapps_brain.federation import (
    FederationConfig,
    load_federation_config,
    save_federation_config,
)

# Load (creates defaults if file missing)
config = load_federation_config()

# Inspect
print(config.hub_path)        # optional override; empty => ~/.tapps-brain/memory/federated.db
print(config.projects)        # list[FederationProject]
print(config.subscriptions)   # list[FederationSubscription]

# v3: hub is PostgreSQL — configure via TAPPS_BRAIN_FEDERATION_DSN, not a file path.
# config.hub_path is a v2 field (SQLite era); ignored in v3.
```

`FederationConfig` fields:

| Field           | Type                         | Description                        |
|-----------------|------------------------------|------------------------------------|
| `hub_path`      | `str`                        | **v2 / SQLite legacy field** — ignored in v3. In v3 the federation hub is PostgreSQL; configure via `TAPPS_BRAIN_FEDERATION_DSN`. |
| `projects`      | `list[FederationProject]`    | Registered projects                |
| `subscriptions` | `list[FederationSubscription]`| Active subscriptions              |

## Filtering

### Publish filtering

When publishing to the hub, you can control which memories are shared:

- **Scope filter**: Only entries with `scope="shared"` are eligible for `sync_to_hub()`. Project-scoped and branch-scoped entries are never published.
- **Key filter**: Pass `keys=[...]` to `sync_to_hub()` to publish a specific subset of shared entries.
- **Tag-based selection**: Use `FederatedStore.search(..., tags=[...])` on the hub side to query by tags.
- **Confidence threshold**: Hub search accepts `min_confidence` to skip low-confidence entries.
- **Memory group**: `FederatedStore.search(..., memory_group="...")` restricts results to entries with a specific partition label.

Currently, publish filtering is performed by the caller (select entries, then call `publish()`). A declarative filter DSL is planned for future releases that would allow specifying publish rules in `federation.yaml`:

```yaml
# Planned (not yet implemented):
publish_filter:
  min_confidence: 0.7
  tags_require_any: ["shared", "core"]
  tiers_exclude: ["context", "ephemeral"]
  memory_groups: ["team-a"]
```

### Subscribe filtering

Subscriptions already support declarative filtering:

- **`sources`**: Limit which projects to pull from (empty = all).
- **`tag_filter`**: Only import entries with at least one matching tag.
- **`min_confidence`**: Skip entries below a confidence threshold.

These filters are applied during `sync_from_hub()` before any data enters the local store.

---

## Conflict Handling

### Current behavior: local-wins

When `sync_from_hub()` encounters a key that already exists in the local store, the local version is always kept. The hub entry is skipped and counted as a `conflict` in the sync result:

```python
result = sync_from_hub(store, hub, project_id="my-api")
# result: {"imported": 3, "skipped": 1, "conflicts": 2}
#                                       ^^^^^^^^^^^^
#                           2 hub keys already existed locally
```

This is a deliberate design choice: **local data is authoritative**. Federation is a supplement, not a replacement for local decisions.

### What happens when a subscriber edits a federated row locally

Once an entry is imported from the hub, it becomes a normal local entry (tagged with `"federated"` and `"from:<project>"`). If the subscriber edits it locally:

1. **The local edit is preserved** -- the hub version will not overwrite it on the next sync (local-wins rule).
2. **The hub retains the original** -- the publisher's version remains unchanged in the hub.
3. **No write-back** -- local edits are never pushed back to the hub automatically. The subscriber's changes stay local.

### Detecting divergence

To detect when local federated entries have diverged from the hub:

```python
# Compare local federated entries with hub versions
local_entries = store.search("", tags=["federated"])
for entry in local_entries:
    hub_versions = hub.search(entry.key, project_ids=[source_project])
    if hub_versions and hub_versions[0]["value"] != entry.value:
        print(f"Diverged: {entry.key}")
```

### Future considerations

- **Merge policies** (analogous to Hive conflict policies) may be added to allow subscribers to choose between local-wins, hub-wins, or confidence-max semantics.
- **Bi-directional sync** with conflict markers would enable collaborative editing of shared knowledge, but adds significant complexity.

---

## Design Decisions

1. **Explicit-only sharing** -- memories are never published automatically.
   A project must explicitly call `sync_to_hub()` or `FederatedStore.publish()`.

2. **Local-wins conflict resolution** -- when syncing from the hub, if the
   local store already has an entry with the same key, the local version is
   kept. This prevents external data from overwriting local decisions.

3. **No auto-sharing** -- there is no background process or hook that pushes
   memories. All federation operations are synchronous and user-initiated.

4. **Composite primary key** -- the hub uses `(project_id, key)` as the
   primary key, so different projects can have entries with the same key
   without collisions.

5. **Provenance tracking** -- imported entries are tagged with their source
   project for auditability.

6. **PostgreSQL + tsvector** -- the hub uses the same PostgreSQL stack (tsvector + pgvector) as the local store (ADR-007).
