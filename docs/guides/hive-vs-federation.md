# Hive vs federation — when to use which

Both features move memories across boundaries, but the **boundary** and **mechanics** differ. Use this page first, then deep-dive in [`hive.md`](hive.md) or [`federation.md`](federation.md).

| | **Hive** | **Federation** |
|---|----------|------------------|
| **Goal** | Share memory **across agents** on a machine (or coordinated agents) | Share memory **across projects** via an explicit hub |
| **Store** | `~/.tapps-brain/hive/hive.db` | `~/.tapps-brain/memory/federated.db` (default) |
| **Trigger** | Propagation rules + `agent_scope` (`private` / `domain` / `hive`) | **Explicit** publish + subscribe + sync (no automatic cross-project push) |
| **Good for** | Assistants, skills, and tools that should read/write the same live pool | Monorepos, org templates, or projects that opt in to a shared catalog |
| **Not for** | Replacing project-local truth — each project still has its own `.tapps-brain/memory/memory.db` | Live multi-agent coordination — use Hive |

**Can I use both?** Yes. A project can keep a local store, publish a **subset** (`scope="shared"`) to federation, **and** propagate selected tiers to Hive for agents.

**Project-local `memory_group`** (partition inside one repo) is separate from Hive **namespaces** and from federation rows; federation hub rows can carry `memory_group` so subscribers preserve the publisher’s label (GitHub **#51**).

**Ground truth:** `docs/engineering/system-architecture.md` and `docs/engineering/call-flows.md`.
