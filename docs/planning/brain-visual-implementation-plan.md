# Implementation plan: dynamic tapps-brain visual identity

Track progress here for a **modern, per-instance-unique** visual representation of tapps-brain (dashboard, marketing, OpenClaw surfaces, or docs). Scope is intentionally large; phases are ordered so early slices ship value without committing to WebGPU or full 3D.

**Status:** planning  
**Last updated:** 2026-03-30

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

(Add a row per milestone or PR.)

---

## Related docs

- Product architecture: root `CLAUDE.md`
- Delivery queue: [`open-issues-roadmap.md`](open-issues-roadmap.md)
- Planning conventions: [`PLANNING.md`](PLANNING.md)
