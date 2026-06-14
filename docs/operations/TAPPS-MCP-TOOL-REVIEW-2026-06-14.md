# TappsMCP Tool Review — findings for tapps-mcp (2026-06-14)

**Context:** Quality audit of `tapps-brain` using TappsMCP v3.12.27 MCP tools only.
**Audience:** tapps-mcp maintainers — not tapps-brain code fixes.
**Session:** `tapps_session_start`, `tapps_doctor`, `tapps_dead_code`, `tapps_dependency_scan`, `tapps_dependency_graph`, `tapps_audit_campaign`, `tapps_report`, `tapps_validate_config`, `tapps_validate_changed`, `tapps_quick_check`, `tapps_security_scan`.

---

## Summary

| Tool | Status | Severity |
|------|--------|----------|
| `tapps_session_start` | OK | — |
| `tapps_doctor` | OK (7 expected consumer drift checks) | — |
| `tapps_dead_code` | OK | — |
| `tapps_dependency_scan` | Works; misleading scan source | Medium |
| `tapps_dependency_graph` | Under-reports edges (known TAP-2035) | High |
| `tapps_audit_campaign` | Plan mode OK; clusters show 0 imports | High |
| `tapps_report` | OK; score semantics unclear vs quick_check | Medium |
| `tapps_validate_config` | GitHub Actions not auto-detected | Low |
| `tapps_validate_changed` | OK | — |
| `tapps_quick_check` | OK | — |
| `tapps_security_scan` | B101 on test files inflates report counts | Low |

---

## Issue 1 — `tapps_dependency_graph` under-reports import edges

**Observed (tapps-brain):**
- `total_modules`: 396
- `total_edges`: 48
- `cycles.total`: 0

A package with 140+ modules under `src/tapps_brain/` cannot have only 48 import edges. Confirmed by manual inspection: modules like `store.py`, `backends.py`, and `retrieval.py` import each other extensively.

**Cross-reference:** Audit campaign output cites `TAP-2035` — `build_import_graph` 0-edge bug on monorepo roots; workaround is `graph_root` param.

**Impact:** `tapps_dependency_graph` coupling metrics and `vulnerability_impact.most_exposed_modules` are unreliable for layout decisions. `tapps_audit_campaign` clustering inherits the same broken graph (all sessions report `0 internal imports, 0 boundary imports`).

**Recommendation:**
1. Fix root-cause in `build_import_graph` when `project_root` uses `src/` layout (add `src` to `sys.path` or resolve package names from `pyproject.toml`).
2. Document required `graph_root` until fixed.
3. Add regression test with a minimal `src/pkg/` fixture expecting >N edges.

---

## Issue 2 — `tapps_audit_campaign` clusters are import-decoupled

**Observed:** All 19 planned sessions for `src/tapps_brain` show:
```
intra_edges: 0
boundary_edges: 0
rationale: "9 files under 'src.tapps_brain' (0 internal imports, 0 boundary imports)"
```

**Expected:** Sessions should group tightly coupled modules (e.g. `store` ↔ `backends` ↔ `postgres_private`).

**Root cause:** Same as Issue 1 — empty/sparse import graph.

**Recommendation:** Block `mode=plan` with a warning when `total_edges / total_modules < 0.1`, or auto-set `graph_root` from `pyproject.toml` `[tool.setuptools.packages.find]` / `[project.scripts]`.

---

## Issue 3 — `tapps_dependency_scan` scans MCP environment, not project lockfile

**Observed:**
```json
{
  "scan_source": "environment",
  "scanned_packages": 126,
  "vulnerable_packages": 1,
  "by_severity": { "high": [{ "package": "torch", "installed_version": "2.12.0", "vulnerability_id": "CVE-2025-3000" }] }
}
```

**Problem:** torch is an optional dev/eval dependency in tapps-brain (`evaluation.py` lazy-imports it). The scan reflects the uv tool env where torch is installed, not necessarily `pyproject.toml` runtime deps. No `fixed_version` was returned.

**Recommendation:**
- Add `scan_source: lockfile | environment` to response (already partially present — make lockfile the default when `uv.lock` / `poetry.lock` exists).
- Scan declared optional groups separately (`--extra reranker`, etc.).
- Surface `fixed_version` when pip-audit provides it; flag when empty.

---

## Issue 4 — Score discrepancy: `tapps_report` vs `tapps_validate_changed` / `tapps_quick_check`

**Example — `src/tapps_brain/agent_brain.py`:**

| Tool | Score | Gate |
|------|------:|------|
| `tapps_report` (project scan) | 72.4 | PASS |
| `tapps_validate_changed` (quick) | 100.0 | PASS |
| `tapps_quick_check` | (not run on this file alone in batch) | — |

**Example — `src/tapps_brain/backends.py`:**

| Tool | Score | Gate |
|------|------:|------|
| `tapps_report` | 64.0 | FAIL |
| `tapps_quick_check` | 66.7 | FAIL |

Quick mode vs full scoring is documented, but `tapps_report` gate PASS at 72.4 while overall threshold is 70 confuses consumers. `agent_brain` at 72.4 passes gate in report but would fail if threshold were 75.

**Recommendation:**
- Align gate threshold display in `tapps_report` markdown table (show PASS/FAIL reason).
- Document that `tapps_validate_changed(quick=true)` skips type-check depth that lowers `tapps_report` scores.

---

## Issue 5 — `tapps_validate_config` does not detect GitHub Actions

**Observed on `.github/workflows/ci.yml`:**
```json
{
  "config_type": "unknown",
  "valid": true,
  "suggestions": ["Could not detect config type. Specify config_type explicitly."]
}
```

**Recommendation:** Add `github_actions` auto-detection for paths matching `.github/workflows/*.yml`. Optional checks: pinned action SHAs, `permissions:` block, secret usage patterns.

---

## Issue 6 — `tapps_security_scan` counts test asserts as security issues

**Observed on `examples/agentforge_bridge/test_brain_bridge.py`:**
- `passed: true`, `total_issues: 28`, all Bandit `B101` (assert in tests)
- `tapps_report` aggregates these into `total_security_issues: 29` for the project sample

**Recommendation:**
- Auto-skip B101 when path matches `test_*.py`, `tests/**`, or `# nosec B101` present.
- Or add `include_tests: false` parameter (default false for security aggregation in `tapps_report`).

---

## Issue 7 — `tapps_doctor` brain probe latency message is ambiguous

**Observed:**
```
"tapps-brain probe latency": "probe latency: unavailable (/metrics HTTP 403)"
```

Doctor overall `ok: true` but the message reads like a failure. `/metrics` 403 may be expected when metrics auth is enabled.

**Recommendation:** Downgrade to `info` when health/auth probes pass; clarify "metrics endpoint requires separate auth (optional)".

---

## Issue 8 — `tapps_report` default file selection is alphabetical, not priority-based

**Observed:** First 50 files include `examples/`, `scripts/archive/ralph/`, and early `src/` modules — not core hot paths. Gate failures in critical modules may be missed until a full scan.

**Recommendation:** Add `scope=src/` or `sort=impact` (via import graph hub score once Issue 1 is fixed).

---

## Repro commands (tapps-brain repo root)

```bash
# Session + doctor
tapps-mcp doctor

# Project scans
tapps-mcp dead-code --scope project
tapps-mcp dependency-scan
tapps-mcp dependency-graph

# Audit plan
# (MCP) tapps_audit_campaign(mode=plan, scope=src/tapps_brain)

# Report
tapps-mcp report --format markdown --max-files 50

# Config
tapps-mcp validate-config --file-path docker/docker-compose.hive.yaml
tapps-mcp validate-config --file-path .github/workflows/ci.yml
```

---

## Suggested tapps-mcp stories (for their backlog)

1. **Fix import graph for src-layout packages** (TAP-2035 follow-up) — P1
2. **Audit campaign: warn on sparse graph** — P2
3. **dependency_scan: prefer lockfile over tool env** — P2
4. **validate_config: GitHub Actions detector** — P3
5. **security_scan/report: exclude test B101 by default** — P3
6. **doctor: clarify metrics 403 probe message** — P3

---

*Generated by tapps-brain agent session 2026-06-14. File path: `docs/operations/TAPPS-MCP-TOOL-REVIEW-2026-06-14.md`*
