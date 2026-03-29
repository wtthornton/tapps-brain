# Design note: multi-scope memory (#49)

Epic **#49** (multi-group memory scopes: Hive, named groups, personal) needs a clear
separation of concepts before implementation. Three axes overlap today in discussion;
they should stay distinct in the product model.

## 1. Named groups (project-local)

**Intent:** Partition memories inside a single project database (e.g. `team-a`, `feature-x`)
for retrieval, UI, or policy without sharing across machines.

**Sketch:** Optional string `group` (or `collection`) on `MemoryEntry`, indexed for filter
and recall; CLI/MCP `group=` parameters; default empty = “ungrouped / global within project.”

**Dependencies:** Schema + migration; retrieval and MCP surfaces; docs and profile hints.

## 2. Hive namespaces (cross-agent, shared store)

**Intent:** Already implemented: shared `hive.db` with **namespaces** (`universal`, profile-
aligned domains). This is **cross-agent**, not an arbitrary project tag.

**Sketch:** Extend behavior only where product asks for new namespace rules, visibility,
or push/pull ergonomics—not by aliasing “named groups” to Hive without an explicit mapping.

**Dependencies:** Hive store schema/API; propagation rules; MCP/CLI already partially covered.

## 3. Profile scopes (tier/layer semantics)

**Intent:** `MemoryProfile` defines **layers** (tier names, half-lives, promotion). This is
**classification and decay**, not storage partitioning.

**Sketch:** Keep profile as the source of tier validity; any “scope” language in profiles
should not be overloaded to mean Hive namespace or named group without renaming in docs.

## Suggested implementable issues (split from epic)

1. **Schema + model:** Add optional project-local `group` (or agreed name) on memories;
   migration; persistence and FTS if needed.
2. **Retrieval:** Filter and rank by `group`; default “all groups” for backward compatibility.
3. **MCP/CLI:** `memory_save` / search / recall parameters; list groups if exposed.
4. **Hive alignment doc:** Table mapping “when to use Hive namespace vs project group vs
   profile layer”; no code or small doc-only PR.
5. **Optional:** Federated or export format carrying `group` for relay/import.

Work should land in that order (1→2→3) with 4 as parallel documentation. Issue 5 is optional
until a concrete consumer exists.

## Out of scope for first slice

- Renaming Hive “namespace” to “group” globally (high churn, breaks mental model).
- Implicit sync between project `group` and Hive namespace without explicit user/agent action.

## Actionable child issues (file on GitHub)

Copy-paste titles, acceptance criteria, and dependency order from
[`epic-49-tasks.md`](epic-49-tasks.md) into child issues; track real issue numbers in
[`open-issues-roadmap.md`](open-issues-roadmap.md).
