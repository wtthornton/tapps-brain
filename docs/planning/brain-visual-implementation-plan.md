# Implementation plan: dynamic tapps-brain visual identity

> **Note — storage references:** Completed phases A–B in this plan reference `sqlite-vec` fields
> (`sqlite_vec_rows`, `sqlite_vec_enabled`). These were implemented against the SQLite-era store.
> ADR-007 (2026-04-11) removed SQLite entirely; the equivalent fields in v3.4+ reflect PostgreSQL /
> pgvector state. New phases should reference `retrieval_mode: pgvector` and pgvector diagnostics
> rather than sqlite-vec fields.

Track progress here for a **modern, per-instance-unique** visual representation of tapps-brain (dashboard, marketing, OpenClaw surfaces, or docs). Scope is intentionally large; phases are ordered so early slices ship value without committing to WebGPU or full 3D.

**Status:** planning (Phase A–E expanded dashboard spec added)  
**Last updated:** 2026-04-02

---

## Vision (summary)

- **Dynamic:** visuals react to live or snapshot store metrics (counts, tiers, decay signals, Hive/Federation, diagnostics). Prefer **event-oriented** updates (append/slice) over full rerenders where the stack allows ([streaming dashboard patterns](https://midrocket.com/en/guides/ui-design-trends-2026/)).
- **Unique per brain:** deterministic **fingerprint** → palette, motion seeds, graph layout seeds, typography axes (same store → same “personality”; no opaque random art). This doubles as **algorithmic transparency**: the user can see that the look is seeded from known inputs, not a black-box generator.
- **2026 bar:** meet the principles in the next section across layout, motion, access, performance, and honesty.

---

## 2026 forward-thinking principles (design bar)

These are the **explicit** criteria used to double-check deliverables. They synthesize current professional UI direction (modular layouts, refined translucency, Web-capable 3D, AI-accompanying interfaces) with accessibility work underway through **WCAG 3** (outcomes-based model, broader scope including cognitive load and emerging surfaces). WCAG 3 remains in **working draft** as of early 2026; ship **WCAG 2.2 AA** as the legal/contractual floor while tracking WCAG 3 outcomes where they sharpen UX.

| Theme | What “forward” means for tapps-brain |
|-------|--------------------------------------|
| **Layout** | **Bento / modular grids** with real hierarchy (hero + supporting tiles); implement with **CSS Grid + subgrid** where it reduces hacks and improves responsive density. |
| **Surfaces** | **Glass/translucency 2.0**: thin `backdrop-filter`, optional grain, **gradient borders**, elevation on **dark-first** / **OLED-friendly** palettes (true black where it helps contrast and power). |
| **Typography** | **Variable fonts** + **kinetic type** only when tied to **data** (e.g. load/stress), not decorative noise; respect `prefers-reduced-motion`. |
| **Motion** | **View Transitions API** (or equivalent) for state changes (snapshot refresh, diagnostics mode) so motion **explains** state, not just decorates. |
| **Depth / 3D** | **Functional 3D only** (comprehension: tier as depth, federation as satellite layer). WebGPU is **progressive enhancement**, not the MVP gate. |
| **Scale** | **WebGPU or GPU-style paths** optional for large series/graphs; **downsampling** (e.g. LTTB-style) and **caps** for particles/nodes are mandatory for mid-tier devices. |
| **Multimodal-ready** | Controls and key story beats work with **keyboard**; consider **touch targets** and future **voice/assistant** hooks if this ships in OpenClaw-adjacent surfaces—without blocking v1. |
| **Cognitive clarity** | WCAG 3 emphasis on comprehension: **plain-language** labels, “aggregated vs exact graph” disclaimers, diagnostics **legibility over beauty**. |
| **AI-era UI honesty** | If any ML-adjacent or generative asset appears later, it must be **labeled**; default story stays **deterministic** (fingerprint + store stats). |
| **Privacy** | Snapshot pipeline is **aggregated and sanitized** by default; document what never leaves the machine. |

**References (external):**

- [W3C WCAG 3.0 Working Draft](https://www.w3.org/TR/wcag-3.0) — outcomes model; immersive/spatial scope emerging.
- [Midrocket: UI design trends 2026](https://midrocket.com/en/guides/ui-design-trends-2026/) — bento, evolved glassmorphism, dark-first maturity, WebGPU + intentional 3D, variable/kinetic type.

---

## 2026 dashboard trend digest (for this surface)

Use these as **design review checkpoints**, not as a mandate to ship every gimmick. They align tapps-brain’s story (**deterministic, trustworthy, local-first memory**) with what operators expect from modern analytics UIs.

| Trend | What it means here | Source / further reading |
|-------|-------------------|----------------------------|
| **Decision-first, not data-dump** | Above the fold: *“Is this brain healthy for recall?”* (circuit, retrieval mode, capacity). Charts support a decision; they are not the hero. | [Think Design — Dashboard Design in 2026](https://think.design/blog/dashboard-design-in-2026-dos-and-donts/) |
| **Bento / modular hierarchy** | Asymmetric grid: one **anchor tile** (fingerprint + trust), supporting tiles for retrieval, Hive, federation, storage. Already matches NLT demo direction; evolve density with **container queries**. | [Midrocket 2026 UI trends](https://midrocket.com/en/guides/ui-design-trends-2026/), [Design Signal — patterns](https://designsignal.ai/articles/dashboard-design-patterns) |
| **Ambient / peripheral metrics** | Secondary stats (rate-limit anomalies, save-phase p50) as **muted pulses** or small spark strips—felt at a glance, not read like a spreadsheet. | [Design Signal — ambient visualization](https://designsignal.ai/articles/dashboard-design-patterns) |
| **Glass 2.0 + solid content** | Sticky header / overlays: light blur; **card bodies stay solid** for contrast (OLED-friendly dark theme as equal citizen to light). | [Spunk — UI trends 2026](https://spunk.pics/blog/ui-design-trends-2026) |
| **AI-era honesty** | Any future ML-heavy viz must be **labeled**; default remains **fingerprint-seeded, reproducible** chrome. Behavioral copy: *what* is aggregated, *what* never ships. | [LetsBlogItUp — AI-first dashboards 2026](https://letsblogitup.dev/articles/building-a-data-driven-dashboard-for-2026-s-ai-first-design-trends/) |
| **Accessibility as default** | **WCAG 2.2 AA** floor; keyboard path through tabs/sections; **`prefers-reduced-motion`** turns ambient motion into static bands; optional **`prefers-reduced-data`** skips nonessential charts. | Same WCAG / trend refs above |

**Inspiration parity (NLT “inside” pages):** Long-form investor interiors (e.g. rich sections, phase timelines, agent run telemetry on sibling products) set a **density expectation**. tapps-brain should feel **related** in craft (typography, section rhythm, “how it was built” transparency) while staying **smaller in scope**: one project’s memory ops, not a full PE memo. Treat Complio-style pages as **layout and narrative density** references, not as a requirement to duplicate financial content.

---

## Marketing & UX narrative (“why this screen exists”)

**One-line promise:** *A beautiful, honest control room for your second brain—no memory text leaves the pipe unless you choose a local-trust mode.*

**Audience split:**

| Audience | Job-to-be-done | UX emphasis |
|----------|----------------|-------------|
| **Solo dev / operator** | “Is recall broken? Is Hive on? Am I full?” | Traffic-light health, retrieval mode, capacity, diagnostics circuit |
| **Team lead** | “Are we consistent across machines?” | Fingerprint compare, export timestamp, schema/profile version |
| **NLT / storytelling** | “This is real infrastructure, not a toy” | Density, craft, fingerprint + “deterministic identity” story |

**Story beats (scroll order):**

1. **Trust strip** — Fingerprint (copy), snapshot time, snapshot-format version vs DB schema (labeled unambiguously).
2. **Pulse** — Entry count, tier mix (mini chart), agent_scope breakdown (private / domain / hive / `group:*` counts).
3. **Retrieval & RAG reality** — `retrieval_effective_mode`, sqlite-vec rows, hybrid summary (mirror `health_check` logic so the UI matches CLI/MCP).
4. **Hive & federation** — Attached, namespace count, agent count, federation project count (from existing health paths).
5. **Integrity & safety** — Tamper counts, relations count, rate-limit anomaly totals (aggregated).
6. **Activity & memory economics** — Histograms: `access_count` distribution buckets, optional tag **frequency** (privacy tiered).
7. **Groups** — Count of distinct `memory_group` values; optional **local** mode: top groups by count (names can be sensitive—gated).
8. **Diagnostics** — Full block when export includes it; explain *omitted* when `--skip-diagnostics`.
9. **Privacy footer (amplified)** — Three bullets: what is always excluded, what is aggregated, what **local verbose** adds.

**Microcopy principles:** Plain language, no fake “AI confidence” scores unless tied to real diagnostics; prefer *“BM25 + vector (sqlite-vec)”* over *“RAG enabled”* unless you add a tooltip defining RAG in this product.

---

## Telemetry → UI module map (“include everything”)

Everything listed is **already available or cheap to aggregate** from `MemoryStore` / persistence / `run_health_check` / metrics. The work is **contract + UI**, not inventing new backend science.

| Domain | Source (Python) | Safe aggregate (default export) | Local / verbose add-on |
|--------|-----------------|----------------------------------|-------------------------|
| Identity | `visual_snapshot` identity + fingerprint | Fingerprint, profile, store path, schema versions | Same |
| Entries & capacity | `store.health()` | `entry_count`, `max_entries`, `oldest_entry_age_days` | Optional utilization % callout |
| Tiers | `tier_distribution` | Bar / donut | Trend if time-series added later |
| Agent scope | `list_all` → counts | `agent_scope_counts` (expand for `group:x` normalization) | Per-group rollup table |
| Memory groups | `list_memory_groups()` + counts | **Count only** | Named groups + entry counts |
| Tags | `list_all` → tag frequencies | **Omit** or top-N hashed buckets | Top tags by count (leakage warning in UI) |
| Access / recall signal | `access_count`, `total_access_count`, `useful_access_count` | Sum, mean, histogram buckets, “hot vs cold” ratio | Percentiles |
| Relations | `relation_count` | Stat + optional “density” ratio vs entries | — |
| Retrieval / vector | `_retrieval_health_from_store`, sqlite vec helpers | Mode + summary + vec rows | Link to docs |
| Hive | `run_health_check` hive slice | Connected, entries, agents, namespaces list | — |
| Federation | `store.health()` | Enabled flag + project count | — |
| Consolidation / GC | `store.health()` | Candidate counts | — |
| Rate limits | `store.health()` | Anomaly counters | — |
| Save path perf | `get_metrics()` / `save_phase_summary` | Compact line + optional mini bar chart | Full histograms in local JSON |
| Integrity | `store.health()` | Verified / tampered / no_hash; cap tampered keys | Full key list local-only |
| Diagnostics | `store.diagnostics()` | Composite + circuit + timestamp | History sparkline if exported |
| Package | `package_version` | Visible in header | — |

**Fingerprint v2 consideration:** Extending the hashed identity payload when new aggregates are added **changes** the fingerprint for the same logical store—version the rules and document migration (e.g. `identity_schema_version` inside the hashed object).

---

## Privacy tiers (export + UI)

| Tier | CLI flag (proposed) | JSON | Who |
|------|---------------------|------|-----|
| **Strict** | `--privacy strict` | No store path; no tag names; group count only; minimal PII | Shareable screenshots |
| **Standard** | default today + expansions | Aggregates + path + retrieval mode; no tag histogram | Teammates / docs |
| **Local verbose** | `--privacy local` | Tag top-N, named groups, tampered key list cap, richer metrics | Same machine only |

Implementation must **never** put raw memory `value` text in snapshot JSON unless explicitly out of scope (separate feature).

---

## Revised execution phases (expanded dashboard)

Phases 0–5 above remain valid; this block **sequences the “everything dashboard”** without collapsing the earlier hero/3D vision.

### Phase A — Snapshot schema v2 + parity with CLI health

- [x] Bump `schema_version` to **2** in `VisualSnapshot` (demo still reads v1 JSON with fallbacks).
- [x] Add **`retrieval_effective_mode`**, **`retrieval_summary`**, **`sqlite_vec_rows`**, **`sqlite_vec_enabled`** (via `retrieval_health_slice()` + store properties; properties not callables fixed in `health_check` too).
- [x] Add **`hive_health`** slice (namespaces, agents, entries) via best-effort `HiveStore` open.
- [x] Add **`memory_group_count`**, optional **`memory_group_counts`** (`local` only).
- [x] Add **`access_stats`**: buckets + sum/mean (no per-key).
- [x] Add **`tag_stats`**: `local` only (top N); omitted standard/strict.
- [x] Add **`identity_schema_version`** on snapshot + inside hashed identity payload.
- [x] Tests: `test_visual_snapshot.py` + CLI export assertion; strict/local coverage.
- [x] Docs: `examples/brain-visual/README.md`, `docs/guides/visual-snapshot.md`.

### Phase B — `examples/brain-visual/index.html` IA overhaul

- [x] Load row + theme toggle + **Copy fingerprint**; privacy tier surfaced in KPI strip from JSON.
- [ ] **Compare** second snapshot file (A/B diff) — not implemented.
- [x] **Section anchor nav** + blocks: Pulse / Retrieval / Hive / Activity / Groups / Tags / Integrity / Privacy (Diagnostics remains in bento + `#diagnostics`).
- [x] **KPI strip** (entries, DB schema, privacy tier, Hive hub, tier row sum).
- [x] **Bar charts** (CSS): tier mix + access histogram.
- [x] **Empty / legacy states:** v1 export messaging; Hive unreachable copy; no tag_stats copy.
- [ ] **View Transitions API** — not implemented.
- [ ] **Formal WCAG audit** — inherits existing reduced-motion on tiles; manual pass still open.

### Phase C — Marketing polish

- [ ] **Hero line** under title: rotating *value props* (deterministic, local, Hive-aware)—data-bound to real flags, not Lorem ipsum.
- [ ] **“How to read this”** collapsible: fingerprint, diagnostics circuit, retrieval modes.
- [ ] Optional **export poster** button: SVG or PNG card of KPI strip for slides (client-side only).

### Phase D — Optional live bridge (later)

- [ ] Local HTTP server or MCP tool returning snapshot JSON (behind auth/off by default).
- [ ] **SSE or poll** for metrics refresh (aligns with streaming dashboard direction); document threat model.

### Phase E — Distribution

- [ ] Link from `open-issues-roadmap.md` when Phase A lands.
- [ ] `STATUS.md` one-liner if marketed beyond examples.

---

## Goals

- [ ] Ship at least one **primary surface** (choose in Phase 0): e.g. static marketing hero, in-repo demo page, OpenClaw plugin asset, or CLI-opened local HTML.
- [ ] Define and document a **fingerprint spec** (inputs + hash + parameter mapping).
- [ ] Expose a **small JSON snapshot API** (or documented contract) from Python so any frontend can render without reimplementing store logic.
- [ ] **Accessibility:** **WCAG 2.2 AA** baseline on default theme; track applicable **WCAG 3 draft outcomes** (cognitive clarity, multimodal where relevant); **`prefers-reduced-motion`** and **high-contrast** variants; test with keyboard and screen reader **where HTML UI exists**.
- [ ] **2026 layout/motion:** bento shell implemented with **subgrid** *or* documented exception; at least one **View Transitions** (or documented fallback) for major state change (e.g. snapshot refresh).
- [ ] **Performance:** document **caps** and fallback **static poster** / degraded tier for `prefers-reduced-data` or low-power mode if feasible.

## Non-goals (initially)

- [ ] No **opaque** LLM-generated imagery at runtime (conflicts with deterministic product story unless explicitly labeled and optional).
- [ ] No requirement to visualize **every** memory as a node on day one (sampling / aggregation is OK).
- [ ] No blocking dependency on **WebGPU** (progressive enhancement).
- [ ] No **VR/AR-only** requirement; spatial cues are **screen-first** (optional future layer if WCAG 3 immersive guidance matures for your surface).

---

## Phase 0 — Decisions and contract

- [ ] **Pick primary surface(s)** and audience (developers only vs public marketing).
- [ ] **Stack choice:** pure static (SVG/CSS/Canvas) vs React/Vite vs integration into existing `openclaw-plugin` build.
- [ ] **Data source:** snapshot file (`brain-visual.json`) vs HTTP endpoint vs MCP tool returning layout JSON (align with security: local-only vs served).
- [ ] **Privacy defaults:** explicit list of fields **excluded** from snapshot (raw memory text, PII patterns); aggregation only for v1.
- [ ] **User preferences contract:** how the UI honors `prefers-reduced-motion`, optional `prefers-reduced-data`, and high-contrast (tokens or alternate theme).
- [ ] **Fingerprint v1 inputs** (draft list — trim in design review):
  - [ ] Project or store id (stable string)
  - [ ] Schema / profile version
  - [ ] Counts by tier; optional counts by `agent_scope`
  - [ ] Totals: entries, sessions indexed, consolidation events (if cheap)
  - [ ] Flags: Hive enabled, federation path present, diagnostics circuit state
- [ ] **Acceptance:** one-page “contract” section in this doc or a linked `brain-visual-contract.md` with example JSON **versioned** (`schema_version`).

---

## Phase 1 — Data bridge (Python)

- [ ] Add a **single function or CLI** that emits a versioned snapshot dict for visualization (e.g. `tapps_brain.visual_snapshot` or `python -m tapps_brain.visual_export`).
- [ ] Snapshot includes fingerprint inputs + **pre-aggregated** stats (histograms, top tags if available, last N activity buckets — keep PII out).
- [ ] **Streaming-readiness (optional v1.1):** document a minimal **event or delta** shape for future live ingest/recall pulses (even if v1 only does full snapshot refresh).
- [ ] Unit tests: snapshot shape stable, deterministic given fixed store fixture.
- [ ] Document how often to refresh (on demand vs watch).

---

## Phase 2 — Visual system (2D first)

- [ ] **Design tokens:** dark-first / **OLED-friendly** palette, spacing, **refined** glass (subtle blur, optional noise, gradient border tokens).
- [ ] **Layout:** **bento** shell with **CSS subgrid** where it simplifies responsive card rhythm; **container queries** for tile density if static HTML/CSS.
- [ ] **Typography:** one **variable** font; kinetic rules **data-bound**; static fallbacks for reduced motion.
- [ ] **Motion:** **View Transitions** (or listed fallback) for snapshot-driven updates; no infinite decorative loops as the only mode.
- [ ] **Fingerprint → params** implementation (8–12 parameters max for v1): hue shift, flow angle, particle budget band, graph seed, etc.
- [ ] **Reduced motion:** disable or simplify continuous animation; preserve structure and hierarchy.
- [ ] **Acceptance:** static export (PNG/SVG) or screenshot baseline for one golden fingerprint (optional visual regression).

---

## Phase 3 — Hero visual (medium complexity)

Choose **one** primary metaphor (can add second later). **Intent check:** static or mild 2.5D beats spectacle; 3D must **encode** tier, time, or federation—not generic “spinning brain.”

- [ ] **Option A — Flow field / particles:** Canvas 2D or WebGL; nodes = aggregated clusters, not 1:1 entries at scale.
- [ ] **Option B — Force-directed graph:** sampled nodes/edges from similarity or parent links if available; otherwise synthetic topology from stats.
- [ ] **Option C — Temporal strip / helix:** time-ordered activity (sessions or ingest buckets).

**Shared:**

- [ ] **Recall / ingest pulse:** optional event channel (WebSocket or polling) or manual “refresh snapshot” only for v1.
- [ ] **Hive / federation:** distinct secondary layer (color, stroke, or satellite panel)—**spatial separation as information**.
- [ ] **Diagnostics:** circuit breaker / warning state overrides palette (**legibility first**, cognitive load down).

---

## Phase 4 — Performance and scale

- [ ] Define **caps** (max nodes, max particles, downsample strategy — **LTTB-style** for time series if applicable).
- [ ] **Main thread budget:** consider **Worker** offload for layout sampling or heavy aggregation when the shell is web-based.
- [ ] **WebGPU path (optional):** spike graph or field at N>10k points; **fallback** to Canvas 2D/WebGL or simplified view.
- [ ] **Bundle size budget** if shipped inside OpenClaw plugin or PyPI wheel (lazy load or external asset).

---

## Phase 5 — Distribution and docs

- [ ] **README or guide** section: how to generate snapshot, open demo, embed in slides.
- [ ] **Accessibility note** in user-facing docs: WCAG 2.2 AA target; WCAG 3 outcomes tracked; how to enable high-contrast / reduced motion.
- [ ] **OpenClaw / packaging:** if applicable, asset pipeline and version pinning.
- [ ] **Link from** [`open-issues-roadmap.md`](open-issues-roadmap.md) and/or a GitHub epic when execution starts.
- [ ] **STATUS.md** note if this becomes an advertised feature.

---

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Scope creep (full 3D product) | Ship Phase 2 + one hero from Phase 3 before WebGPU; **functional 3D** review gate. |
| Fingerprint churn breaks visuals | Version the snapshot schema; golden tests on JSON shape. |
| Performance on weak laptops | Caps + reduced quality tier + **static poster** fallback; optional `prefers-reduced-data`. |
| Misleading metaphor (users think graph = exact memory graph) | **Plain labels:** “aggregated view” / “sampled topology”; cognitive clarity per WCAG 3 direction. |
| WCAG 3 draft churn | **WCAG 2.2 AA** as shipping bar; periodically re-check [WCAG 3 draft](https://www.w3.org/TR/wcag-3.0) for outcome labels that map to this UI. |

---

## Open questions

- [ ] Which surface ships first?
- [ ] Should the visual run **entirely offline** from a JSON file (no server)?
- [ ] Any **branding lock** (logo, wordmark) that must stay fixed while only the “brain” varies?

---

## Progress log

| Date | Note |
|------|------|
| 2026-03-30 | Plan created; phases 0–5 defined. |
| 2026-03-30 | Pass: **2026 principles** table (WCAG 3 trajectory, bento/subgrid, View Transitions, OLED-first glass, WebGPU as progressive enhancement, cognitive honesty, privacy). |
| 2026-03-30 | **Slice shipped:** `tapps_brain.visual_snapshot` + CLI `visual export` + `tests/unit/test_visual_snapshot.py` + `examples/brain-visual/` static bento demo. |
| 2026-04-02 | **Plan expanded:** 2026 dashboard trend digest (cited), marketing/UX narrative, full telemetry→UI map, privacy tiers (`strict` / standard / `local`), Phases A–E for “everything” dashboard + NLT inside-page density parity (craft, not financial content). |
| 2026-04-02 | **Phase A + B executed:** `VisualSnapshot` schema **v2** (`access_stats`, `hive_health`, retrieval/sqlite-vec fields, `privacy_tier`, `memory_group_*`, optional `tag_stats`); CLI `--privacy`; `retrieval_health_slice()` public; `health_check` sqlite-vec property handling; `examples/brain-visual/` dashboard sections + KPI strip + copy fingerprint; docs/README updates. |
| 2026-04-02 | **Help coverage:** KPI + scorecard summary strips, inner tiles (Pulse chart, retrieval mode, Hive hub detail, access histogram, issue draft), dual pills on Entries / DB schema / Hive / Pulse / Privacy; new `HELP_CONCEPTS` articles (`kpi_strip`, `scorecard_counts`, `issue_ticket_draft`, `memory_profile`, `federation_snapshot`); **fix:** Diagnostics bento `data-help` uses `scorecard:diagnostics_bento` (article lives in `HELP_SCORECARD`). Documented in `visual-snapshot.md` and `examples/brain-visual/README.md`. |

(Add a row per milestone or PR.)

---

## Related docs

- Product architecture: root `CLAUDE.md`
- Delivery queue: [`open-issues-roadmap.md`](open-issues-roadmap.md)
- Planning conventions: [`PLANNING.md`](PLANNING.md)
