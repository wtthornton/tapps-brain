# Phase 2 — Follow-up issues (ready to file)

**Purpose:** Concrete, prioritized cleanup items derived from `docs/engineering/code-inventory-and-doc-gaps.md` and follow-up code review (2026-03-31).

**How to use:** Copy each **Issue block** into a new GitHub issue. Suggested labels are listed per item; adjust for your repo convention.

---

## Priority legend

| Tier | Meaning |
|------|---------|
| **P0** | User-facing wrong behavior or docs that mislead operators. |
| **P1** | Discoverability / consistency; engineering clarity. |
| **P2** | Nice-to-have polish, epic-sized work, or repo hygiene. |

---

## P0 — Misleading product or operator contracts

### Issue: ED-P0-01 — Fix or implement `FederationConfig.hub_path`

**Labels:** `documentation`, `federation`, `bug?` (or `enhancement` if implementing)

**Problem:** `hub_path` is persisted in `~/.tapps-brain/memory/federation.yaml` but `FederatedStore` uses the default `federated.db` path unless `db_path=` is passed explicitly. Operators who set `hub_path` may believe the hub moved; it may not.

**Acceptance criteria (pick one):**

- **A)** Document-only: field renamed or clearly marked deprecated with migration note; *or*
- **B)** Code: `load_federation_config()` / sync paths pass `Path(config.hub_path)` when non-empty; tests cover custom path.

**Refs:** `src/tapps_brain/federation.py` (`FederationConfig.hub_path`), `docs/guides/federation.md`, `docs/engineering/optional-features-matrix.md`.

---

### Issue: ED-P0-02 — Align Hive “default” story (CLI vs MCP vs library vs profile docstring)

**Labels:** `documentation`, `hive`, `cli`

**Problem:**

- CLI `_get_store()` always passes `hive_store=HiveStore()`.
- MCP defaults `enable_hive=True` but supports `--no-enable-hive`.
- `HiveConfig` docstring says *“When absent or all defaults, Hive is effectively disabled”* — that conflates **profile hive rules** with **whether a `HiveStore` is attached**; attaching Hive changes recall merge behavior regardless.

**Acceptance criteria:**

- [ ] `profile.py` `HiveConfig` docstring updated to describe propagation rules only, not global Hive on/off.
- [ ] `docs/guides/hive.md` has a short “Who attaches Hive?” table: CLI default, MCP flag, `MemoryStore(..., hive_store=None)`.
- [ ] Optional follow-up issue: CLI `--no-hive` / env toggle for parity with MCP (if product wants symmetry).

**Refs:** `src/tapps_brain/cli.py` (`_get_store`), `src/tapps_brain/mcp_server.py`, `src/tapps_brain/profile.py`, `docs/guides/hive.md`.

---

## P1 — Discoverability and engineering baseline

### Issue: ED-P1-01 — Link `docs/engineering/` from root README and onboarding

**Labels:** `documentation`

**Problem:** Canonical engineering baseline exists but is not linked from primary entry points.

**Acceptance criteria:**

- [ ] `README.md` (or `docs/guides/getting-started.md`) links to `docs/engineering/README.md` as implementation ground truth.
- [ ] Optional one-line in `CLAUDE.md` / `.cursor/rules/project.mdc` pointing maintainers at `docs/engineering/`.

**Refs:** `docs/engineering/README.md`

---

### Issue: ED-P1-02 — Document or wire OpenTelemetry exporter (`otel_exporter`)

**Labels:** `documentation`, `observability`, `tech-debt`

**Problem:** `README.md` lists `otel_exporter` under observability. `create_exporter` / `OTelExporter` appear **unused** from `store`, `cli`, or `mcp_server` in runtime paths — only `tests/unit/test_otel_exporter.py` exercises the module. Operators cannot know if OTel is “on” or how to enable it.

**Acceptance criteria (pick one):**

- **A)** Engineering docs: `docs/guides/observability.md` (or section in `docs/engineering/`) states “OTel module exists; not wired to MCP/CLI; see EPIC-032”; README table footnote updated; *or*
- **B)** Product: wire minimal exporter init behind optional flag/env and document.

**Refs:** `src/tapps_brain/otel_exporter.py`, `README.md` observability table, `docs/planning/epics/EPIC-032.md`, `tests/unit/test_otel_exporter.py`.

---

### Issue: ED-P1-03 — User-facing doc for visual snapshot / `visual export`

**Labels:** `documentation`

**Problem:** CLI exposes `visual export`; `visual_snapshot.py` and `examples/brain-visual/` exist but `docs/guides/` has no operator guide.

**Acceptance criteria:**

- [ ] New `docs/guides/visual-snapshot.md` (or subsection under an appropriate guide): purpose, CLI usage, JSON contract pointer to `brain-visual-implementation-plan.md` / examples.
- [ ] Link from `README.md` or feature list if the feature is supported.

**Refs:** `src/tapps_brain/cli.py` (`visual`), `src/tapps_brain/visual_snapshot.py`, `examples/brain-visual/README.md`, `docs/planning/brain-visual-implementation-plan.md`.

---

### Issue: ED-P1-04 — Reconcile MCP tool/resource counts across docs

**Labels:** `documentation`

**Problem:** Historical drift: “64 tools”, “8 resources”, older “7 URIs” mentions appear in different files. Source of truth should be generated manifest or a single counted section.

**Acceptance criteria:**

- [ ] Single canonical sentence: “Counts as of build X: see `docs/generated/mcp-tools-manifest.json`” (or script output).
- [ ] Grep-driven cleanup: `docs/guides/mcp.md`, `docs/guides/openclaw.md`, `docs/planning/STATUS.md` — no contradictory counts unless dated as historical.

**Refs:** `docs/generated/mcp-tools-manifest.json`, `scripts/check_openclaw_docs_consistency.py` (if applicable).

---

## P2 — Hygiene and deep audits

### Issue: ED-P2-01 — Vendored `mem0-review/` tree documentation

**Labels:** `documentation`, `chore`

**Problem:** Large tree under `mem0-review/` can be mistaken for product code; it is reference/research.

**Resolution (2026):** Issue #61 documented the distinction; **`mem0-review/` was removed from the repository** entirely so it is not confused with product code.

**Acceptance criteria (historical):**

- [x] `docs/engineering/README.md` noted scope (later superseded by removal).
- [x] Tree deleted — no vendored Mem0 checkout in tapps-brain.

**Refs:** *(none — directory removed)*

---

### Issue: ED-P2-02 — Dead-code / orphan module sweep (tracked list)

**Labels:** `tech-debt`, `documentation`

**Problem:** Phase 1 listed process; need a one-time pass with owners.

**Acceptance criteria:**

- [ ] Run import/static reference check from CLI/MCP entrypoints; extend `docs/engineering/code-inventory-and-doc-gaps.md` with **Resolved** / **Won’t fix** rows.
- [ ] Create issues for any module confirmed orphaned beyond tests.

---

## Tracking

| ID | Priority | Title | GitHub |
|----|----------|-------|--------|
| ED-P0-01 | P0 | Federation `hub_path` contract | [#55](https://github.com/wtthornton/tapps-brain/issues/55) |
| ED-P0-02 | P0 | Hive default / docstring alignment | [#56](https://github.com/wtthornton/tapps-brain/issues/56) |
| ED-P1-01 | P1 | Link `docs/engineering` from README | [#57](https://github.com/wtthornton/tapps-brain/issues/57) |
| ED-P1-02 | P1 | OTel exporter document or wire | [#58](https://github.com/wtthornton/tapps-brain/issues/58) |
| ED-P1-03 | P1 | Visual snapshot guide | [#59](https://github.com/wtthornton/tapps-brain/issues/59) |
| ED-P1-04 | P1 | MCP count drift cleanup | [#60](https://github.com/wtthornton/tapps-brain/issues/60) |
| ED-P2-01 | P2 | `mem0-review` documentation | [#61](https://github.com/wtthornton/tapps-brain/issues/61) |
| ED-P2-02 | P2 | Dead-code sweep | [#62](https://github.com/wtthornton/tapps-brain/issues/62) |

**Filed:** 2026-03-31 via GitHub API (engineering doc follow-up pack).
