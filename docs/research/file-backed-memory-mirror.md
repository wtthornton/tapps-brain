# File-backed memory mirror — feasibility report (TAP-2095)

**Status:** spike complete · **Recommendation:** **build narrower** — ship a one-shot `brain_export --layout=managed-agents` instead of a continuous `--mirror-dir`. **Author:** Claude Agent · **Date:** 2026-05-17 · **Epic:** [TAP-2092](https://linear.app/tappscodingagents/issue/TAP-2092).

This report verifies the Anthropic Managed Agents pattern cited in the 2026-05-17 NLT roadmap, maps it onto the tapps-brain architecture, sketches the wire-up that would be required for a continuous mirror, surfaces the risks, and recommends a narrower one-shot export instead. No source code changes were made.

---

## 1. Anthropic Managed Agents pattern — verified

Cross-referenced against the primary source ([platform.claude.com/docs/en/managed-agents/memory](https://platform.claude.com/docs/en/managed-agents/memory), fetched 2026-05-17). Material facts:

| Fact | Value | Notes |
|------|-------|-------|
| Beta header | `managed-agents-2026-04-01` | The roadmap's "2026-04-23 public beta" date is close but the beta header itself is dated 2026-04-01. Treat both dates as plausible (header date vs press date). |
| Mount layout | `/mnt/memory/<store-name>/` per attached store | Up to **8 stores** per session, attached only at session creation. |
| Subfolder structure | **None prescribed.** Paths are free-form. | Examples in the docs: `/preferences/formatting.md`, `/archive/2026_q1_formatting.md`. No `working/`, `episodic/`, `semantic/` directories appear anywhere in the official spec. |
| Per-memory size cap | 100 kB (~25k tokens) | "Structure memory as many small focused files, not a few large ones." |
| Read/write API | Standard agent file tools (`bash`, `read`, `write`, `edit`, `glob`, `grep`) plus REST `memories.{create,retrieve,update,delete}` | No dedicated memory tool; the filesystem **is** the API. |
| Concurrency | Optimistic via `content_sha256` precondition; mismatch → re-read + retry | Anthropic does NOT solve lost-update behind the scenes; the client must re-read. |
| Versioning | Every change creates an immutable `memver_...`; 30-day retention; `redact` endpoint for compliance scrubbing | Live `memories.retrieve` always returns head; version endpoints expose history. |
| Access modes | `read_write` (default) or `read_only` per attachment | Read-only is the recommended default for reference material. |

**Discrepancy with the NLT roadmap:** the roadmap cites a specific `memory/working/`, `memory/episodic/`, `memory/semantic/` folder layout as if Anthropic prescribes it. That structure is **not in the official docs.** It is a convention — likely borrowed from CoALA-style cognitive architectures — that NLT (or whoever authored that roadmap row) is layering on top of an otherwise schema-free filesystem. Any tapps-brain export that targets this layout is targeting a *convention*, not a vendor contract. That matters for our design: we have flexibility on the subfolder names, and we should not promise compatibility with a layout that has no authoritative spec.

---

## 2. tapps-brain analogue map

| Concept | Anthropic Managed Agents | tapps-brain today |
|---------|--------------------------|-------------------|
| Storage substrate | Filesystem on the agent's container | Postgres ([ADR-007](../adr/ADR-007.md)); in-memory dict + Postgres write-through ([store.py:404-450](../../src/tapps_brain/store.py#L404-L450)) |
| Address | Path string (e.g. `/preferences/formatting.md`) | `(project_id, agent_id, key)` composite ([store.py:409](../../src/tapps_brain/store.py#L409)) |
| Decay model | Out of scope — the agent curates manually | Tier-based exponential decay: architectural=180d, pattern=60d, procedural=30d, context=14d, ephemeral/session=1d ([decay.py:64-83](../../src/tapps_brain/decay.py#L64-L83)) |
| Cross-agent sharing | Multiple sessions can attach the same store | Hive (Postgres `hive_*` tables; cross-agent recall with `agent_scope`) |
| Versioning | Every write → immutable `memver_...`, 30-day retention | Postgres history table + diagnostics history ([migrations 003-004](../../src/tapps_brain/migrations/private/)); operator-tier audit log |
| Search | grep / glob / agent reads | BM25 + pgvector HNSW + RRF fusion ([retrieval.py](../../src/tapps_brain/retrieval.py), [fusion.py](../../src/tapps_brain/fusion.py)) |
| Tier vocabulary | None; agent invents subfolders | `architectural` / `pattern` / `procedural` / `context` / `ephemeral` / `session` ([models.py:113](../../src/tapps_brain/models.py#L113), `MemoryTier`) |

**The honest mapping:**

- **`/memory/architectural/`** ↔ tapps-brain `tier=architectural` (long-lived decisions, half-life 180d)
- **`/memory/patterns/`** ↔ tapps-brain `tier=pattern` (conventions, half-life 60d)
- **`/memory/procedures/`** ↔ tapps-brain `tier=procedural` (workflows, half-life 30d)
- **`/memory/context/`** ↔ tapps-brain `tier=context` (session-scope facts, half-life 14d)

This is a clean 1:1 — no fancy CoALA-style "working/episodic/semantic" translation needed. The tapps-brain tier vocabulary is more granular than "working/episodic/semantic" and arguably more useful for agent-engineering purposes (you can pick a half-life per tier, you cannot pick a half-life for "episodic" without inventing your own decay).

**The retrieval-stack mapping is the harder problem.** When a Managed Agent reads `/mnt/memory/`, all it has is grep over flat markdown. tapps-brain's value-add — BM25 ranking, vector similarity, RRF fusion, tier-decay-aware compositing — disappears. A file mirror is structurally a **lossy export of tapps-brain's retrieval layer**, not a peer storage. This is the foundational tradeoff for the rest of the report.

---

## 3. Wire-up sketch (paper only)

For a continuous `--mirror-dir` flag on `MemoryStore`, the integration points would be:

```
MemoryStore.__init__(..., mirror_dir: Path | None = None)
    src/tapps_brain/store.py:334-450  (constructor signature + persistence resolution)

MemoryStore.save() → MirrorWriter.upsert(entry)
    on every save → write {tier}/{key}.md with frontmatter + value

MemoryStore.archive() / .forget() → MirrorWriter.delete(key)
    on every soft-delete → remove the mirror file

decay.recalculate_confidence() → MirrorWriter.upsert(entry)
    src/tapps_brain/decay.py — decay is lazy, so this would fire on read,
    massively amplifying writes on the hot path (see Risks §4.1)

consolidation.merge() → MirrorWriter.replace(consolidated_entry, removed_keys)
    auto-consolidation merges duplicates — the mirror would need to know
    BOTH which keys disappear and which new key appears

GC.archive_stale() → MirrorWriter.delete(key)
    background GC sweeps need to keep the mirror in sync
```

**Layout** (per-project, scoped to the running process's `project_id`):

```
<mirror_dir>/
  manifest.json                        # mirror metadata (last_full_sync, count by tier)
  architectural/
    <key>.md                            # frontmatter (created_at, confidence, tags) + value
  patterns/
    <key>.md
  procedures/
    <key>.md
  context/
    <key>.md
  ephemeral/    # optional — skip by default; 1-day half-life makes mirroring noisy
```

**Top-N policy** (per the story): instead of mirroring every entry, mirror only the **top-N most recent or highest-confidence per tier** — say 200 per tier. This bounds disk footprint at a few MB regardless of project size and matches the Managed Agents guidance ("many small focused files"). Eviction in the mirror would follow recall ranking, not raw access time.

**Frontmatter shape** (so the file is both human-readable and re-importable):

```markdown
---
key: agentforge-vendored-wheel-consumption
tier: architectural
confidence: 0.91
created_at: 2026-04-12T14:22:00Z
last_accessed: 2026-05-17T18:31:00Z
source: human
tags: [agentforge, integration]
---

AgentForge consumes tapps-brain via a vendored wheel under vendor/.
Not on PyPI; releases reach AgentForge only via manual cp; TAP-995 plans
the HTTP-only migration.
```

This is recoverable: a future `brain_import --layout=managed-agents` could re-hydrate from the same files.

---

## 4. Risks

### 4.1 Write amplification on the hot path

Every `MemoryStore.save()` already does: in-memory dict update + Postgres write + (optionally) Hive propagation + embedding generation. Adding a synchronous file-write makes the recall path slower for every consumer — including the ones that never read the mirror. Worse, **decay is lazy** ([decay.py:5](../../src/tapps_brain/decay.py#L5)): confidence is recomputed on read. A naive "mirror on decay" implementation would emit a disk write on every recall, turning every read into a write. Mitigation: async mirror writes via a bounded queue, or **don't do continuous mirroring at all** (see §6).

### 4.2 Cache coherence and source-of-truth ambiguity

Postgres is the source of truth ([ADR-007](../adr/ADR-007.md)). If an operator edits a `.md` file directly on disk (which the layout invites — the whole point is "the filesystem is the API"), the mirror diverges silently. Reconciliation paths multiply: read-only mirror (file edits ignored), bidirectional sync (last-write-wins or conflict-detect), or one-way export (drop the file, regenerate from Postgres). Each has different operator surprises. Mitigation: make the mirror **explicitly read-only** with a `.tapps-brain-mirror-readonly` sentinel file, or — better — ship a one-shot export instead so the question never arises.

### 4.3 Secrets in plaintext

Postgres can run pg_tde for at-rest encryption; flat markdown on a developer's disk cannot. Even with `MemoryGuard` filtering, agents that have ingested an API key, a customer name, or a session token would write that data to disk in plaintext. Mitigation: **redaction pass on export** (regex-based scrub for AWS keys, GitHub tokens, JWTs, emails) — this is the same engine the existing `tapps-mcp` security_scan already uses. The mirror should never receive entries with `source=human + tags contains [secret]` regardless.

### 4.4 Operator confusion ("which is the source of truth?")

When an operator runs `tools/audit_consumers.py` and sees the mirror folder, they will edit the markdown. The next `MemoryStore.save()` overwrites their edit. Or, if writes are bidirectional, their edit takes effect, but they have no audit trail (Postgres history table doesn't see direct file writes). **Either model is surprising.** Mitigation: prefix every mirror file with a banner comment (`<!-- READ-ONLY — managed by tapps-brain. Edits ignored. -->`) and document that the mirror is a derived view.

### 4.5 Maintenance burden of a second read path

If the mirror exists, agents WILL build tools that read it. Six months later, when tapps-brain's tier vocabulary changes (e.g. `procedural` splits into `procedural` + `runbook`), the mirror schema and every downstream tool needs to update in lockstep. Mitigation: version the mirror layout in `manifest.json` (`schema_version: 1`) and treat layout changes as breaking.

### 4.6 Downstream tooling proliferation

Once `--mirror-dir` ships, NLT will build a "memory linter" that reads the mirror. AgentForge will build a "memory viewer" that reads the mirror. Each becomes a tapps-brain consumer that **cannot use brain_recall** — they bypass the retrieval stack. tapps-brain's BM25+HNSW+RRF improvements become irrelevant to those consumers. The mirror, by existing, fragments the ecosystem. Mitigation: only ship the mirror if the consumer story explicitly cannot use the MCP/HTTP surface (see §5).

---

## 5. Cost/benefit — who reads the mirror?

The acceptance criteria require at least one concrete consumer story. There is exactly one that holds up:

### Consumer story 1 — Managed Agents containers without outbound network

A team running Anthropic Managed Agents wants to seed an agent with project-specific architectural decisions before each session. Per the docs, the way to do this is to attach a memory store to the session at creation time. The store is mounted at `/mnt/memory/<store-name>/` and the agent reads it via `bash` / `grep` / `read`. **The Managed Agent container cannot make outbound HTTP calls to a self-hosted tapps-brain** — Anthropic does not document a way to whitelist arbitrary endpoints from inside the sandbox, and even if they did, the latency hit of an HTTP roundtrip per recall is unattractive.

For this team, `brain_recall` is not an option. The mirror folder IS the option. Without it, they have to hand-curate the markdown — duplicating the work of the brain.

**This is the only consumer story that survives scrutiny.** Every other story I tried to construct ("operator wants to grep the brain", "agent without MCP wants to read decisions", "we want backups") collapses on inspection:

- "Operator wants to grep" → `brain_recall` with `query="..."` is faster and ranked, and `tools/audit_consumers.py`-style CLIs are the right pattern.
- "Agent without MCP" → it has HTTP if it has anything; tapps-brain's `/v1/recall` works fine.
- "Backups" → `pg_dump` is the answer. The mirror is not a backup; it's a lossy projection.

### What becomes possible vs. impossible today

| Capability | Today | With continuous `--mirror-dir` | With one-shot `brain_export` |
|------------|-------|-------------------------------|------------------------------|
| Seed a Managed Agent session at creation time | Manual curation | ✓ if mirror is fresh enough | ✓ run export → upload via `memories.create` API |
| Operator browses brain offline | `tools/audit_consumers.py` or HTTP | ✓ but stale risk | ✓ — re-export when needed |
| Continuous "live mirror" for external dashboards | — | ✓ | ✗ |
| Agent inside a sandbox queries the brain | — | ✓ (read flat files) | ✓ (snapshot at session start) |
| Sub-second freshness | ✓ (Postgres direct) | ✓ but expensive | ✗ |

The only column where continuous-mirror beats one-shot-export is **continuous external dashboards**. We have no such consumer today. NLT's roadmap story C.2 says "local file-backed memory" — that does **not** require sub-second freshness; a session-start snapshot is fine.

---

## 6. Recommendation: build narrower

**Build a one-shot `brain_export --layout=managed-agents <dir>` command, NOT a continuous `--mirror-dir` flag on `MemoryStore`.**

### Rationale

The cost/benefit table above shows that every viable consumer story is satisfied by a session-start snapshot. Continuous mirroring buys "sub-second freshness for external dashboards" — a capability with no current customer. Meanwhile it pays write-amplification on the hot path, cache-coherence operator confusion, and a maintenance bill that will keep growing as the tier vocabulary evolves. Trading those costs for a capability nobody has asked for is a net loss.

A one-shot export inverts every risk:

- **Risk 4.1 (write amplification):** eliminated — export runs on demand, never on the recall path.
- **Risk 4.2 (cache coherence):** eliminated — the export is a snapshot with a timestamp in `manifest.json`. There is no "live mirror" to diverge from. Operators understand snapshots.
- **Risk 4.3 (secrets):** still real but contained — the export is the redaction boundary; a single security pass before write.
- **Risk 4.4 (source-of-truth):** the timestamped snapshot is obviously a snapshot; nobody confuses a `2026-05-17_brain_export/` folder for the live store.
- **Risk 4.5 / 4.6 (maintenance + downstream tools):** still real, but at least the mirror schema is reviewed *once per export*, not per save.

It also slots cleanly into the Anthropic Managed Agents lifecycle: stores are attached at session creation; the `brain_export → memories.create` pipeline is the natural seed step. There is no Anthropic-side affordance that exploits continuous mirroring, so there's no Anthropic-side value to capture by building it.

### What "build narrower" means in code (for the follow-on story)

- New CLI / MCP tool: `brain_export(output_dir: Path, layout: str = "managed-agents", redact: bool = True, top_n_per_tier: int = 500)`.
- Service function in `services/memory_service.py` next to `audit_consumers`.
- Writes `<dir>/manifest.json` + `<dir>/{tier}/{key}.md` files per the §3 layout.
- Redaction pipeline reuses the existing `MemoryGuard` patterns plus a tighter regex pack (AWS keys, GH tokens, JWTs, emails).
- Operator-facing, deferred-loading like `brain_audit_consumers` (TAP-2093).
- Estimated 2-3 points.

A future continuous `--mirror-dir` can always be built later if a real consumer materializes (current answer: nobody has one). The reverse — pulling back a continuous mirror that's already in production — is harder.

---

## 7. Follow-on work

- **File:** new story under epic [TAP-2092](https://linear.app/tappscodingagents/issue/TAP-2092) — `brain_export --layout=managed-agents` one-shot export. Wire to the `tools/` directory alongside [`tools/audit_consumers.py`](../../tools/audit_consumers.py) (TAP-2093). Estimated 2-3pt. Filed by Claude Agent.
- **Do not file:** continuous `--mirror-dir` flag. Defer until a consumer with sub-second-freshness needs surfaces. Record this report URL in the rejection rationale so the question doesn't get relitigated.
- **Coordinate with NLT:** the NLT roadmap row C.2 ("local file-backed memory") should consume the one-shot export rather than reinventing it. Surface this report to the NLT planning agent.

---

## 8. References

- Anthropic Managed Agents — memory API: <https://platform.claude.com/docs/en/managed-agents/memory>
- Anthropic blog — built-in memory for Managed Agents: <https://claude.com/blog/claude-managed-agents-memory>
- Anthropic skills repo (managed-agents-memory): <https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/managed-agents-memory.md>
- ADR-007 (Postgres-only persistence): `docs/adr/ADR-007.md`
- [TAP-2092](https://linear.app/tappscodingagents/issue/TAP-2092) — parent epic (consumer adoption surface)
- [TAP-2095](https://linear.app/tappscodingagents/issue/TAP-2095) — this spike
- [TAP-2093](https://linear.app/tappscodingagents/issue/TAP-2093) — `brain_audit_consumers` (sibling pattern for operator-facing tools)
