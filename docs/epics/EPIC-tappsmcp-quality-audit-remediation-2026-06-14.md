# TappsMCP quality audit remediation (2026-06-14)

<!-- docsmcp:start:metadata -->
**Status:** Proposed
**Priority:** High

<!-- docsmcp:end:metadata -->

---

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

We are doing this so that tapps-brain passes TappsMCP quality gates consistently, closes known dependency CVE exposure, and refreshes stale agent scaffolding identified by tapps_doctor.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:goal -->
## Goal

All src/tapps_brain modules scoring below the standard gate (70) are remediated; torch CVE exposure in evaluation is resolved or documented; tapps-mcp bootstrap artifacts are current.

<!-- docsmcp:end:goal -->

<!-- docsmcp:start:motivation -->
## Motivation

A full TappsMCP tool sweep on 2026-06-14 found 7 doctor failures, 1 high CVE (torch), and 10 gate failures in the first 50 scored files (80% pass rate). Core paths (store, agent_brain, postgres_private) pass at 100 in quick mode but adjacent modules drag down maintainability scores.

<!-- docsmcp:end:motivation -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] - [ ] tapps_doctor reports all_passed=true after tapps-mcp upgrade
- [ ] pip-audit shows zero high/critical CVEs for declared runtime deps
- [ ] All src/tapps_brain/*.py files pass tapps_quick_check (standard preset)
- [ ] docker-compose.hive.yaml suggestions triaged (limits/networks or documented deferral)

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:stories -->
## Stories

### 0.1 -- tapps_upgrade: refresh stale AGENTS.md, skills, remove dual brain MCP

**Points:** TBD

Run tapps-mcp upgrade --force; strip direct tapps-brain from .mcp.json and .cursor/mcp.json per bridge-only policy

**Tasks:**
- [ ] Implement tapps_upgrade: refresh stale agents.md, skills, remove dual brain mcp
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** tapps_upgrade: refresh stale AGENTS.md, skills, remove dual brain MCP is implemented, tests pass, and documentation is updated.

---

### 0.2 -- evaluation.py: resolve torch CVE-2025-3000 exposure

**Points:** TBD

Lazy-import audit, pin/upgrade torch, or isolate eval harness from runtime deps

**Tasks:**
- [ ] Implement evaluation.py: resolve torch cve-2025-3000 exposure
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** evaluation.py: resolve torch CVE-2025-3000 exposure is implemented, tests pass, and documentation is updated.

---

### 0.3 -- backends.py: raise quality gate above 70

**Points:** TBD

Split high-CC factory functions; improve maintainability score from 66.7

**Tasks:**
- [ ] Implement backends.py: raise quality gate above 70
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** backends.py: raise quality gate above 70 is implemented, tests pass, and documentation is updated.

---

### 0.4 -- async modules: gate failures in async_postgres_kg and store helpers

**Points:** TBD

Fix type/maintainability issues in _store_query, _store_relations, async_postgres_kg, cli/hive.py

**Tasks:**
- [ ] Implement async modules: gate failures in async_postgres_kg and store helpers
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** async modules: gate failures in async_postgres_kg and store helpers is implemented, tests pass, and documentation is updated.

---

### 0.5 -- scripts/: gate failures in load_smoke, run_benchmark, validate_epics

**Points:** TBD

Bring dev scripts above standard gate or exclude from scoring scope

**Tasks:**
- [ ] Implement scripts/: gate failures in load_smoke, run_benchmark, validate_epics
- [ ] Write unit tests
- [ ] Update documentation

**Definition of Done:** scripts/: gate failures in load_smoke, run_benchmark, validate_epics is implemented, tests pass, and documentation is updated.

---

<!-- docsmcp:end:stories -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- Document architecture decisions for **TappsMCP quality audit remediation (2026-06-14)**...

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:non-goals -->
## Out of Scope / Future Considerations

- Fixing tapps-mcp tool bugs (filed separately for tapps-mcp repo); full 19-session audit campaign execution

<!-- docsmcp:end:non-goals -->
