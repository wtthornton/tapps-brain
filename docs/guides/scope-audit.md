# Scope Audit: agent_scope / Group / Hive — Allowed Namespaces and Operations

> **EPIC-063 STORY-063.5 / STORY-063.6** — Scope matrix + code checklist.  
> Related: [`hive.md`](hive.md), [`ADR-007`](../planning/adr/ADR-007-postgres-only-no-sqlite.md),
> [`ADR-008`](../planning/adr/ADR-008-rls-deferred.md), [`threat-model.md`](../engineering/threat-model.md)

---

## 1. Scope Matrix — agent_scope → Namespace → Allowed Operations

The `agent_scope` field on every `MemoryEntry` determines where (and whether) the entry
is propagated to the shared Postgres Hive. The local private store and the Hive are
**separate namespaces**; `agent_scope` controls the Hive side only.

### 1.1 Canonical `agent_scope` values

| `agent_scope` | Postgres Hive namespace | Who can read | Who can write | Enforcement point |
|---|---|---|---|---|
| `private` | *(no Hive write)* | Owning agent only | Owning agent only | `PropagationEngine.propagate()` returns `None` early |
| `domain` | Agent's **profile name** (e.g. `thestudio`) | Agents sharing the same profile | Any agent with that profile | `PropagationEngine` maps scope → profile name |
| `hive` | `universal` | **All** Hive agents | Any authenticated Hive agent | `PropagationEngine` maps scope → `"universal"` |
| `group:<name>` | `<name>` (group namespace) | Members of group `<name>` | Members of group `<name>` | `PropagationEngine` checks `agent_is_group_member(name, agent_id)`; denied → warning log, no write |
| `group` *(bare)* | ALL groups the agent belongs to | Members of each group | Owning agent (must be a declared member of every target group) | `MemoryStore.save()` iterates `self._groups`, writes each; group membership validated at store construction via `_setup_group_memberships()` |

> **Note:** `group` (bare, without `:<name>`) is a shorthand for "propagate to every group
> this agent is registered in." It is distinct from `group:<name>` which targets a single
> named namespace.

### 1.2 Scope precedence — profile overrides

Before the namespace is resolved, the agent's active **memory profile** may override `agent_scope`:

| Profile rule | Effect | Priority |
|---|---|---|
| Tier listed in `hive.private_tiers` | Scope forced to `private` regardless of caller value | **Highest** |
| Tier listed in `hive.auto_propagate_tiers` AND scope is `private` | Scope upgraded to `domain` | Medium |
| Neither rule applies | Original `agent_scope` preserved | Lowest |

These overrides happen inside `PropagationEngine.propagate()` unless `bypass_profile_hive_rules=True`
is set (CLI/MCP batch push with `--force`).

### 1.3 Private memory (non-Hive)

Private agent memory is stored in the Postgres private-memory table, keyed by
`(project_id, agent_id)`. No cross-agent read of private rows is possible at the app layer —
queries always filter by the agent's own `agent_id`. See
[`threat-model.md`](../engineering/threat-model.md) for DB-role and RLS context.

### 1.4 Read namespace resolution (recall)

When an agent performs a recall that includes Hive results, the following namespaces are
searched:

1. `universal` — always included
2. Agent's profile-name namespace — domain entries
3. Every Hive group the agent belongs to — group entries

This resolution is implemented in `PostgresHiveBackend.search_with_groups()`.

---

## 2. Code Checklist — Path → Scope Rule → Status

The table below maps every code path that enforces or routes scope decisions, along with
the scope rule it implements and its review status.

| Module / Function | Scope rule enforced | Rule source | Gaps / Notes | Reviewed |
|---|---|---|---|---|
| `agent_scope.py :: normalize_agent_scope()` | Validates that `agent_scope` is one of `private`, `domain`, `hive`, `group`, `group:<name>`; normalises case and whitespace | EPIC-041 STORY-041.2 | Group name character/length limits inherited from `normalize_memory_group()` — same validation as `memory_group`. | 2026-04-11 |
| `models.py :: MemoryEntry._validate_agent_scope` | Field-level validator; calls `normalize_agent_scope()`; rejects invalid values at model construction | EPIC-041 | Runs on every deserialization — covers persistence reads too. | 2026-04-11 |
| `store.py :: MemoryStore.save()` — group membership check (~line 587) | Rejects `group:<name>` save if the store's `_groups` list does not contain `<name>` | STORY-056.3 | **App-layer only.** No DB constraint prevents a misconfigured agent from calling the Hive backend directly. See gap G-1 below. | 2026-04-11 |
| `store.py :: MemoryStore.save()` — bare `group` routing (~line 915) | Writes to ALL groups in `self._groups`; does not re-check individual group membership at save time | STORY-056.3 | Group membership is established at store construction (`_setup_group_memberships()`). If groups are removed from the Hive after store init, stale membership list could allow writes. See gap G-2 below. | 2026-04-11 |
| `store.py :: MemoryStore._setup_group_memberships()` | Auto-creates and joins declared groups in Hive on store init | STORY-056.1 | One-time setup at construction; not re-validated on subsequent saves. | 2026-04-11 |
| `backends.py :: PropagationEngine.propagate()` | Routes `private → no-op`, `domain → profile namespace`, `hive → universal`, `group:<name> → membership check + namespace`; applies profile tier overrides | EPIC-011, STORY-056.3 | `bypass_profile_hive_rules=True` disables tier overrides (intentional for `--force` batch push). | 2026-04-11 |
| `backends.py :: PropagationEngine.propagate()` — group membership check | Calls `hive_store.agent_is_group_member(group_ns, agent_id)`; denies with `hive.propagate.group_denied` warning log when not a member | STORY-056.3 | **This is the Hive-level enforcement.** Only reached via `PropagationEngine`; direct `hive_store.save()` bypasses it. See gap G-1. | 2026-04-11 |
| `postgres_hive.py :: PostgresHiveBackend.save()` | Writes entry to `hive_memories(namespace, key, …)`; no namespace-level access check at the Postgres backend method level | ADR-008 (RLS deferred) | By design — app-layer enforces scope. DB roles (`tapps_runtime`) limit DML; RLS deferred per ADR-008. See gap G-3. | 2026-04-11 |
| `postgres_hive.py :: PostgresHiveBackend.search_with_groups()` | Builds namespace list from `{own_ns, *group_names, "universal"}` for the calling agent's groups | EPIC-056 | No caller-identity enforcement below this layer; relies on app layer passing correct `agent_id`. | 2026-04-11 |
| `postgres_hive.py :: PostgresHiveBackend.agent_is_group_member()` | Checks `hive_group_members(group_name, agent_id)` — DB query | EPIC-056 | Source of truth for membership. DB row is authoritative; `_groups` list in `MemoryStore` is a cached copy. | 2026-04-11 |
| `store.py :: MemoryStore.__init__()` — private memory keying | Private Postgres rows keyed by `(project_id, agent_id)`; recall queries always filter by `self._agent_id` | EPIC-059 STORY-059.5 | Cross-agent private reads structurally impossible at app layer. DB role `tapps_runtime` further restricts DML. | 2026-04-11 |
| `mcp_server.py` — all tool handlers | Tool handlers call `MemoryStore` or `AgentBrain` methods; scope enforcement inherited from store layer | EPIC-062 | MCP tools do not bypass store-layer scope checks. Operator tools behind flag (STORY-062.4). | 2026-04-11 |
| `http_adapter.py` — POST /memory, GET /recall | HTTP adapter delegates to `AgentBrain` / `MemoryStore`; scope enforcement inherited | EPIC-060 | W3C `traceparent` propagated; no scope bypass. Auth middleware required for protected routes (ADR in EPIC-060). | 2026-04-11 |

---

## 3. Identified Gaps

The following gaps were found during this audit. Each is listed with severity and follow-up action.

| ID | Description | Severity | Compensating control | Follow-up |
|---|---|---|---|---|
| **G-1** | `PostgresHiveBackend.save()` has no caller-identity or namespace-membership check. A component that constructs a backend directly and calls `.save()` with an arbitrary namespace bypasses all app-layer scope enforcement. | Medium | `tapps_runtime` DB role limits DML to `hive_memories`/`hive_groups` tables; RLS deferred (ADR-008). In practice, only `PropagationEngine` and trusted store code call `.save()` directly. | File GitHub issue with `security` label: "Hive backend save() should validate caller namespace membership or document trusted-caller contract." |
| **G-2** | `MemoryStore._groups` is populated once at construction. If a group is deleted from the Hive DB after store init, the in-memory list remains stale and the store will attempt to write to a non-existent group namespace. | Low | Writes to a deleted group namespace create rows in `hive_memories` under an orphaned namespace — no security boundary is crossed; next Hive search for that namespace returns those rows to any member of that group (no members → effectively unreachable). | File GitHub issue: "MemoryStore should refresh group membership on reconnect or add TTL on _groups cache." |
| **G-3** | Row-level security on `hive_memories` is deferred (ADR-008). Cross-project reads are prevented only at the app layer via `search_with_groups()` namespace filtering, not at the DB layer. | Medium | ADR-008 documents this decision with risk acceptance; `tapps_runtime` role is DML-only (no DDL); compensating control is documented in [`threat-model.md`](../engineering/threat-model.md). | Per ADR-008: revisit RLS for GA if multi-tenant SaaS is added. No new issue needed — tracked in ADR. |

> **No gaps found:** Cross-agent reads of **private** memory are structurally impossible at
> the app layer (private Postgres table keyed by `(project_id, agent_id)` + recall always
> filters by owning agent). MCP and HTTP paths inherit store-layer enforcement without bypass.

---

## 4. Peer Review Sign-Off

| Reviewer | Date | Sign-off |
|---|---|---|
| *(Hive maintainer)* | — | Pending PR review |
| *(Security reviewer)* | — | Pending PR review |

> Add comments or approval to the PR introducing this document.

### 4.1 Negative-test coverage (STORY-063.7)

Automated negative tests validate the enforcement points above:

| Test file | What it covers |
|---|---|
| `tests/unit/test_scope_negative.py` | `PropagationEngine` group rejection (non-member → None, no save); private scope always dropped; profile `private_tiers` override; `bypass_profile_hive_rules` sanity; cross-tenant write structural isolation (backend keyed by `(project_id, agent_id)` at construction, verified via mocked SQL params); dry-run mode (no writes). |
| `tests/integration/test_private_memory_integration.py::TestTenantIsolation` | Live Postgres round-trip: agent A writes and agent B cannot read (load_all + FTS search); same-agent different-project isolation. |
| `tests/integration/test_rls_spike.py` | Postgres RLS: `hive_admin_bypass` + `hive_namespace_isolation` policies prevent cross-namespace reads when session variable is set. |

---

## 5. References

- [`hive.md`](hive.md) — Hive guide: namespaces, propagation, conflict resolution, scope resolution
- [`hive-vs-federation.md`](hive-vs-federation.md) — When to use Hive vs Federation
- [`memory-scopes.md`](memory-scopes.md) — Local MemoryScope (project/branch/session) — distinct from `agent_scope`
- [`threat-model.md`](../engineering/threat-model.md) — STRIDE one-pager
- [`ADR-007`](../planning/adr/ADR-007-postgres-only-no-sqlite.md) — Postgres-only persistence decision
- [`ADR-008`](../planning/adr/ADR-008-rls-deferred.md) — RLS deferred; compensating controls
- `src/tapps_brain/agent_scope.py` — Scope normalization and validation
- `src/tapps_brain/backends.py` — `PropagationEngine` (scope routing)
- `src/tapps_brain/store.py` — `MemoryStore.save()` (group membership check, group routing)
- `src/tapps_brain/postgres_hive.py` — `PostgresHiveBackend` (DB-level operations)
