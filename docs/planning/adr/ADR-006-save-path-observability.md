# ADR-006: Save-path observability — phase histograms + health summary (defer deeper metrics unless trigger (a))

**Status:** Accepted  
**Date:** 2026-04-03  
**Owner:** @wtthornton  
**Epic / story:** [EPIC-051](../epics/EPIC-051.md) — STORY-051.6  
**Context:** [features-and-technologies.md](../../engineering/features-and-technologies.md) section 10 checklist item 6; [`PLANNING.md`](../PLANNING.md) *Optional backlog gating* (slice **B**)

## Context

Checklist item 6 asked whether to **unify metrics** for save-path latency / consolidation beyond ad hoc logging, including optional **OpenTelemetry**.

Shipped before this ADR (STORY-051.6 implementation themes, 2026-04-02):

- **`MemoryStore.save`** sub-phase **histograms** (`store.save.phase.*` via `MetricsTimer`): lock/build, persist, hive, relations, consolidate, embed.
- Full metrics snapshot on **`store.get_metrics()`** and MCP resource **`memory://metrics`**.
- Compact **`save_phase_summary`** on live store **`health()`** / native health check paths (roadmap tracking row 20 **done**).

**Planning policy:** Slice **B** — *extra save-path observability* beyond `save_phase_summary` — stays **backlogged by default** until trigger **(a)** (*actively tuning or incidenting on save latency / consolidation or GC correlation*) in [`PLANNING.md`](../PLANNING.md).

**Epic checklist:** This ADR satisfies [`EPIC-051`](../epics/EPIC-051.md) **STORY-051.6** / §10 item **6** as a **decision record** alongside the baseline already on `main`; it does **not** require new mandatory metrics or MCP tools unless maintainers later invoke trigger **(a)**.

## Decision

1. **Shipped / maintained path (do):** Keep **save-phase histograms**, **`get_metrics()`**, **`memory://metrics`**, and **`save_phase_summary`** on health as the **standard** operator and integrator surface for save-path timing. No change required to core code for this ADR.

2. **Optional product polish (defer):** **Richer** compact save-phase lines on text **`diagnostics health`** or extra fields on **`HealthReport` JSON** — **deferred** until UX or support asks; not required for checklist closure.

3. **Deeper observability (defer):** Additional counters, structured logs, or **RED**-style SLO dashboards for consolidation/GC correlation — **deferred** unless trigger **(a)** fires.

4. **OTel / unified export:** **OpenTelemetry** remains an **optional extra** ([`EPIC-032`](../epics/EPIC-032.md) GenAI conventions still planned/deferred separately). This ADR does **not** mandate wiring save-phase histograms into OTel spans by default.

## Consequences

- Operators use **MCP `memory://metrics`**, **JSON health**, and existing **CLI** paths for save-phase visibility.
- **No** new mandatory MCP tools or metrics from STORY-051.6 beyond what is already on `main`.

## References

- [`EPIC-051.md`](../epics/EPIC-051.md) — STORY-051.6.
- [`open-issues-roadmap.md`](../open-issues-roadmap.md) — tracking row 20, backlog item 5.
- [`PLANNING.md`](../PLANNING.md) — *Optional backlog gating*.
