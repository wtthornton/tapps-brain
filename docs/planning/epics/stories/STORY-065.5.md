# Story 65.5 -- Agent registry live table

<!-- docsmcp:start:user-story -->

> **As a** tapps-brain operator, **I want** a live table of every registered agent showing agent ID, namespace, scope, and when it last wrote a memory, **so that** I can see which agents are active, which have gone silent, and whether any agent is writing to the wrong namespace

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 5 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that operators of multi-agent Hive deployments can understand agent activity at a glance. The existing dashboard shows only a raw agent count (e.g. "20 registered agents") with no detail. In a 20-agent deployment it is impossible to know which agents are healthy, which stopped writing, and which are misconfigured. The AgentRegistry table in Postgres already holds this data — this story exposes it through /snapshot and renders it in the dashboard.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Add an agent_registry: list[AgentEntry] field to VisualSnapshot (populated only when hive is connected). AgentEntry has: agent_id str, namespace str, scope str, registered_at str, last_write_at str | None. Collect from AgentRegistry.list_agents() in _collect_hive_health() or a new _collect_agent_registry() helper. In the dashboard, add a new Agents section (after Hive hub) with a sortable table: Agent ID | Namespace | Scope | Last Write. Sort default: last_write_at descending (most-recently-active first). Agents silent for >24h get an amber row highlight. Agents never written get a grey row.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `src/tapps_brain/visual_snapshot.py`
- `tests/unit/test_visual_snapshot.py`
- `examples/brain-visual/index.html`
- `examples/brain-visual/brain-visual-help.js`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Add AgentEntry TypedDict with fields: agent_id, namespace, scope, registered_at, last_write_at (nullable) (`src/tapps_brain/visual_snapshot.py`)
- [ ] Add agent_registry: list[AgentEntry] to VisualSnapshot model, populated from AgentRegistry.list_agents() when hive connected (`src/tapps_brain/visual_snapshot.py`)
- [ ] Add _collect_agent_registry(hive_backend) helper that calls AgentRegistry(hive_backend).list_agents() and maps to AgentEntry list (`src/tapps_brain/visual_snapshot.py`)
- [ ] Guard _collect_agent_registry with try/except — return [] if AgentRegistry table does not exist (pre-migration schema) (`src/tapps_brain/visual_snapshot.py`)
- [ ] Add Agents section HTML to index.html after Hive hub section — table with id='agents-table', columns: Agent ID, Namespace, Scope, Last Write (`examples/brain-visual/index.html`)
- [ ] Add renderAgentRegistry(agents) JS function that populates #agents-table, sorts by last_write_at desc, applies amber highlight for >24h silent, grey for null last_write_at (`examples/brain-visual/index.html`)
- [ ] Add Agents link to section nav (`examples/brain-visual/index.html`)
- [ ] Add agent_registry help entry to brain-visual-help.js explaining scope types and last-write interpretation (`examples/brain-visual/brain-visual-help.js`)
- [ ] Add unit tests for _collect_agent_registry with mock AgentRegistry returning known rows (`tests/unit/test_visual_snapshot.py`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] agent_registry in /snapshot payload contains one entry per row in AgentRegistry table
- [ ] AgentEntry fields agent_id
- [ ] namespace
- [ ] scope
- [ ] registered_at
- [ ] last_write_at are all populated
- [ ] Table renders with correct row count matching AgentRegistry
- [ ] Rows sorted by last_write_at descending on render
- [ ] Agents with last_write_at > 24h ago have amber row class
- [ ] Agents with last_write_at == null have grey row class
- [ ] Empty state shown when agent_registry is [] with message 'No agents registered'
- [ ] _collect_agent_registry returns [] without exception when AgentRegistry table does not exist

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [ ] All tasks completed
- [ ] Agent registry live table code reviewed and approved
- [ ] Tests passing (unit + integration)
- [ ] Documentation updated
- [ ] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. Full registry: 3 agents returned
2. table has 3 rows in correct sort order
3. Silent agent: last_write_at 25h ago → amber row class
4. Never-wrote agent: last_write_at null → grey row class
5. No registry table: list_agents() raises → agent_registry=[] no exception
6. Privacy: standard privacy tier → agent_id truncated to first 8 chars + ellipsis

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- AgentRegistry is in postgres_hive.py — check its list_agents() return type; may need to add last_write_at JOIN to hive_entries if not already present
- last_write_at may require a correlated subquery: SELECT MAX(created_at) FROM hive_entries WHERE agent_id = $1
- Agent ID may contain PII in some deployments — document that standard privacy tier omits full agent_id and uses a truncated hash instead

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- STORY-065.1
- STORY-065.4

<!-- docsmcp:end:dependencies -->

<!-- docsmcp:start:invest -->
## INVEST Checklist

- [ ] **I**ndependent -- Can be developed and delivered independently
- [ ] **N**egotiable -- Details can be refined during implementation
- [x] **V**aluable -- Delivers value to a user or the system
- [x] **E**stimable -- Team can estimate the effort
- [x] **S**mall -- Completable within one sprint/iteration
- [x] **T**estable -- Has clear criteria to verify completion

<!-- docsmcp:end:invest -->
