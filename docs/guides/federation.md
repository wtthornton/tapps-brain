# Federation Guide

Cross-project memory sharing via a central hub store.

**Story:** STORY-002.5 from EPIC-002

## Overview

Federation uses a **hub-and-spoke model**. Each project is a spoke that
explicitly publishes shared-scope memories to a central SQLite hub at
`~/.tapps-brain/memory/federated.db`. Projects subscribe to receive
memories from other projects. No data is shared automatically.

```
Project A ──publish──> Hub (federated.db) <──publish── Project B
Project A <──sync────> Hub (federated.db) <──sync────> Project B
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
print(config.hub_path)        # custom hub path or ""
print(config.projects)        # list[FederationProject]
print(config.subscriptions)   # list[FederationSubscription]

# Modify and save
config.hub_path = "/custom/hub/path"
save_federation_config(config)
```

`FederationConfig` fields:

| Field           | Type                         | Description                        |
|-----------------|------------------------------|------------------------------------|
| `hub_path`      | `str`                        | Custom path to federated.db        |
| `projects`      | `list[FederationProject]`    | Registered projects                |
| `subscriptions` | `list[FederationSubscription]`| Active subscriptions              |

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

6. **SQLite + FTS5** -- the hub uses the same WAL-mode SQLite + FTS5 stack
   as the local store for consistency and portability.
