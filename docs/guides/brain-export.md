# brain_export — Managed Agents-layout snapshot exporter

**Status:** added 2026-05-18 ([TAP-2099](https://linear.app/tappscodingagents/issue/TAP-2099)). Recommended in §6 of [docs/research/file-backed-memory-mirror.md](../research/file-backed-memory-mirror.md) ([TAP-2095](https://linear.app/tappscodingagents/issue/TAP-2095) spike).

`brain_export` writes a snapshot of tapps-brain's top-N memories per tier into a folder layout consumable by Anthropic Managed Agents (and any other agent that can read markdown files). It is a **one-shot snapshot**, not a continuous mirror — re-run it whenever you need fresh data.

## When to use it

Use it when an agent runtime cannot reach the tapps-brain HTTP / MCP endpoint at recall time. The canonical case is an Anthropic Managed Agents session: the container has no outbound network whitelisted to your self-hosted brain, but it CAN attach a memory store at `/mnt/memory/<store-name>/` at session creation. The export feeds that pipeline.

Do **not** use it as a backup (use `pg_dump` for ops, or the [native tapps-memory export](memory-import-export.md) for portable project dumps), as a search index (use `brain_recall`), or as a live mirror (the [TAP-2095 spike](../research/file-backed-memory-mirror.md) rejected that path).

For the full format matrix (native JSON, MIF, relay, MEMORY.md, adapters), see [`memory-import-export.md`](memory-import-export.md).

## Running it

### MCP (deployed server)

```
brain_export(
    output_dir="/tmp/brain-export",
    layout="managed-agents",
    redact=True,
    top_n_per_tier=500,
)
```

Returns a JSON envelope with `project_id`, `output_dir`, `tier_counts`, `files_written`, `skipped_secret_tag`, `redacted_fields`, `exported_at`. Refuses to overwrite a non-empty `output_dir` — pick a fresh path each run.

### CLI

```
TAPPS_BRAIN_DATABASE_URL=postgresql://... \
TAPPS_BRAIN_PROJECT_ID=tapps-brain \
python tools/brain_export.py \
  --output-dir /tmp/brain-export \
  --top-n-per-tier 500
```

Pass `--no-redact` to opt out of the redaction pass (entries tagged `secret` are still skipped). Pass `--project-id <id>` to override the env var.

## Output layout

```
/tmp/brain-export/
  manifest.json
  architectural/
    <key>.md
  pattern/
    <key>.md
  procedural/
    <key>.md
  context/
    <key>.md
  ephemeral/   # only when present
    <key>.md
  session/     # only when present
    <key>.md
```

Each `.md` file:

```markdown
<!-- READ-ONLY managed by tapps-brain. Edits ignored. -->
---
key: agentforge-vendored-wheel
tier: architectural
confidence: 0.9100
source: human
created_at: 2026-04-12T14:22:00+00:00
last_accessed: 2026-05-17T18:31:00+00:00
tags: [agentforge, integration]
---

AgentForge consumes tapps-brain via a vendored wheel under vendor/.
```

`manifest.json` carries `schema_version`, `project_id`, `layout`, `exported_at`, per-tier counts, `top_n_per_tier`, `redact`, `skipped_secret_tag`, `redacted_fields`.

## Ranking and truncation

Each tier is ranked by `max(confidence, recency_score)` where `recency_score = 1.0 / (1.0 + age_days / 30.0)`. Highest-ranked `top_n_per_tier` entries are written per tier; the rest are dropped from the snapshot (still present in Postgres).

Entries with `confidence == -1.0` (the "use source default" sentinel) resolve via `_SOURCE_CONFIDENCE_DEFAULTS` — human=0.95, agent=0.6, inferred=0.4, system=0.9.

## Redaction

When `redact=True` (default), values are scrubbed for:

| Kind | Pattern | Replacement |
|------|---------|-------------|
| AWS access keys | `AKIA[0-9A-Z]{16}` | `[REDACTED:aws-key]` |
| GitHub tokens | `gh[pousr]_[A-Za-z0-9_]{30,}` | `[REDACTED:gh-token]` |
| JWTs | `eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+` | `[REDACTED:jwt]` |
| Email addresses | `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}` | `[REDACTED:email]` |

Entries with the `secret` tag are skipped wholesale regardless of `redact` — the tag is the explicit opt-out and never round-trips through the export.

## Snapshot, not a live mirror

Three callouts the [feasibility spike](../research/file-backed-memory-mirror.md) called out and this exporter sidesteps:

1. **Hot-path cost** — the export touches disk only when you run it, never on `MemoryStore.save()` / decay / consolidation.
2. **Cache coherence** — there is no live mirror to diverge from. The `READ-ONLY` banner + `exported_at` timestamp in `manifest.json` make it obvious the folder is a frozen view.
3. **Source of truth** — Postgres remains authoritative ([ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md)). Edits to the markdown files are ignored by the next export (the directory must be empty or a fresh path).

## Related

- [`docs/research/file-backed-memory-mirror.md`](../research/file-backed-memory-mirror.md) — the spike that led to this design.
- [`tools/audit_consumers.py`](../../tools/audit_consumers.py) — sibling operator CLI from TAP-2093.
- [`docs/guides/observability.md`](observability.md) — `recall_quality_metrics` and other operator surfaces from the same epic ([TAP-2092](https://linear.app/tappscodingagents/issue/TAP-2092)).
- Anthropic Managed Agents memory API: https://platform.claude.com/docs/en/managed-agents/memory
