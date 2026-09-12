---
name: tapps-refactor
user-invocable: true
model: claude-sonnet-5
description: >-
  Function-level refactor workflow using call graph tools (Epic 114).
  Use before changing a symbol's signature, deleting a function, or
  refactoring callers — maps blast radius via tapps_call_graph and diff_impact.
allowed-tools: >-
  mcp__nlt-build__tapps_session_start
  mcp__nlt-build__tapps_call_graph
  mcp__nlt-build__tapps_impact_analysis
  mcp__nlt-build__tapps_diff_impact
  mcp__nlt-build__tapps_quick_check
  mcp__nlt-build__tapps_validate_changed
  mcp__nlt-build__tapps_checklist
argument-hint: "[symbol or file-path]"
---
<!-- BEGIN: tapps-skill tapps-refactor v3.12.83 -->
<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->

Symbol-level refactor workflow (Epic 114 / ADR-0017):

1. **Session bootstrap.** Call `mcp__nlt-build__tapps_session_start()` — read `data.call_graph` (`ready`, `stale`, `degraded`). Stale is informational; graph tools auto-rebuild on first use.

2. **Before editing a function.** `mcp__nlt-build__tapps_call_graph(symbol='...', query='callers')` — who calls this symbol? Use `query='callees'` for downstream dependencies or `query='chain'` for bounded chains.

3. **Optional module context.** `mcp__nlt-build__tapps_impact_analysis(file_path='...', symbol='...', granularity='both')` for import + symbol blast radius.

4. **Edit loop.** After each Python file change, `mcp__nlt-build__tapps_quick_check(file_path='...')`.

5. **After edits.** `mcp__nlt-build__tapps_diff_impact(file_paths='...')` or finish with `/tapps-finish-task` (`include_impact` default true refreshes cache).

6. **Close out.** `/tapps-finish-task` with `task_type=refactor` — checklist recommends `tapps_call_graph` and `tapps_diff_impact`.

See `docs/CALL_GRAPH.md` for gap_rate / degraded semantics.
<!-- END: tapps-skill -->

<!-- tapps-skill-project-customizations: preserved from the pre-marker version — review and trim any content the managed block above now covers -->
<!-- flagged: 100% of this region's lines duplicate the managed block above — review and trim -->

<!-- upgrade-policy: overwrite. tapps_upgrade replaces this file wholesale on every run and local edits are lost (tapps_init leaves an existing copy alone; upgrade does not). Fold the change upstream into the platform template, or pin the whole directory with an upgrade_skip_files token. -->

Symbol-level refactor workflow (Epic 114 / ADR-0017):

1. **Session bootstrap.** Call `mcp__nlt-build__tapps_session_start()` — read `data.call_graph` (`ready`, `stale`, `degraded`). Stale is informational; graph tools auto-rebuild on first use.

2. **Before editing a function.** `mcp__nlt-build__tapps_call_graph(symbol='...', query='callers')` — who calls this symbol? Use `query='callees'` for downstream dependencies or `query='chain'` for bounded chains.

3. **Optional module context.** `mcp__nlt-build__tapps_impact_analysis(file_path='...', symbol='...', granularity='both')` for import + symbol blast radius.

4. **Edit loop.** After each Python file change, `mcp__nlt-build__tapps_quick_check(file_path='...')`.

5. **After edits.** `mcp__nlt-build__tapps_diff_impact(file_paths='...')` or finish with `/tapps-finish-task` (`include_impact` default true refreshes cache).

6. **Close out.** `/tapps-finish-task` with `task_type=refactor` — checklist recommends `tapps_call_graph` and `tapps_diff_impact`.

See `docs/CALL_GRAPH.md` for gap_rate / degraded semantics.
