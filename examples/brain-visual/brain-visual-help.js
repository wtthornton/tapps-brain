/**
 * Long-form help for brain-visual scorecard rows and dashboard concepts.
 * Grounded in tapps-brain source (see reference lines on each entry).
 */
(function () {
  "use strict";

  const HELP_SCORECARD = {
    store_entries: {
      title: "Store contents",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>Count of <strong>memory entries</strong> in the project store (the SQLite-backed cache you use via CLI/MCP). " +
            "Empty is normal for a new project; non-zero means facts/preferences/patterns are persisted for recall.</p>",
        },
        {
          heading: "The math",
          html:
            "<p>No formula: it is <code>len(entries)</code> after listing all active memories. Compared against " +
            "<code>max_entries</code> (default 5,000) in the separate capacity check.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Recall, injection, and diagnostics only operate on what is actually stored. Zero entries explains " +
            "empty recall; very high counts stress consolidation, GC, and retrieval latency.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>MemoryStore</code> keeps an in-memory dict with SQLite write-through; saves enforce caps and tiers. " +
            "This row only reflects <em>how many</em> rows exist, not their text.</p>",
        },
      ],
      reference: "Code: <code>MemoryStore.list_all()</code> / <code>store.health().entry_count</code>",
    },

    diagnostics_data: {
      title: "Diagnostics data",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>Whether this JSON export included the <strong>diagnostics</strong> block: composite quality score and " +
            "<strong>circuit state</strong>. If you passed <code>--skip-diagnostics</code>, those fields are omitted for speed.</p>",
        },
        {
          heading: "The math",
          html: "<p>Binary signal in the UI: present vs omitted. No scoring.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Without diagnostics, the scorecard cannot judge circuit or composite from this file—you must re-export or run " +
            "<code>tapps-brain diagnostics health</code>.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>build_visual_snapshot(..., skip_diagnostics=True)</code> skips <code>store.diagnostics()</code> so " +
            "the export stays faster and slightly smaller.</p>",
        },
      ],
      reference: "Code: <code>src/tapps_brain/visual_snapshot.py</code> · CLI <code>visual export</code>",
    },

    diagnostics_bento: {
      title: "Diagnostics (bento tile)",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>The small tile shows the latest <strong>composite score</strong> (0–1) and <strong>circuit state</strong> from the same " +
            "diagnostics run as MCP/CLI. For full rules and thresholds, open the <strong>Scorecard</strong> section and click " +
            "<strong>?</strong> on <em>Diagnostics circuit</em> and <em>Diagnostics composite score</em>.</p>",
        },
        {
          heading: "The math",
          html:
            "<p>Same as EPIC-030: composite = weighted sum of dimension scores; circuit transitions at 0.6 and 0.3 cutoffs " +
            "(and half-open probes after cooldown).</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Quick glance while scrolling the bento; scorecard adds pass/warn/fail triage and ticket export.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>store.diagnostics(record_history=False)</code> during export when not using <code>--skip-diagnostics</code>.</p>",
        },
      ],
      reference: "Code: <code>diagnostics.py</code> · scorecard rows <code>diagnostics_*</code>",
    },

    diagnostics_circuit: {
      title: "Diagnostics circuit",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>A small <strong>finite-state machine</strong> (circuit breaker) that summarizes whether aggregate quality " +
            "is healthy enough to treat recall as “normal.” States: <code>closed</code>, <code>degraded</code>, " +
            "<code>open</code>, <code>half_open</code> (probe after cooldown).</p>",
        },
        {
          heading: "The math / transition rules",
          html:
            "<p>Each diagnostics run computes a <strong>composite score</strong> in <code>[0, 1]</code>. The breaker updates from that value:</p>" +
            "<ul>" +
            "<li><strong>closed</strong> — composite ≥ <strong>0.6</strong></li>" +
            "<li><strong>degraded</strong> — <strong>0.3</strong> ≤ composite &lt; 0.6</li>" +
            "<li><strong>open</strong> — composite &lt; 0.3</li>" +
            "</ul>" +
            "<p>In <strong>half_open</strong> (entered after a cooldown of about <strong>3600s</strong> plus small jitter from " +
            "<code>open</code>), the same 0.6 / 0.3 thresholds decide closed vs degraded vs open. " +
            "Probes can accumulate: after enough probes, composite ≥ <strong>0.45</strong> can bump the state toward " +
            "<code>degraded</code> per <code>record_probe</code>.</p>" +
            "<p>This is deterministic given the composite; it is not ML.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Downstream features (e.g. recall summaries) can surface a <strong>quality warning</strong> when the circuit " +
            "is not <code>closed</code>, so operators know scores/tiers/integrity may be weak before trusting RAG-style injection.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>CircuitBreaker.transition(composite)</code> runs after diagnostics. Optional auto-remediation exists when " +
            "the circuit is <code>open</code> (tier-1 maintenance suggestions based on duplication/staleness/integrity dimensions).</p>",
        },
      ],
      reference:
        "Code: <code>src/tapps_brain/diagnostics.py</code> — <code>CircuitBreaker</code>, <code>CircuitState</code> (EPIC-030)",
    },

    diagnostics_composite: {
      title: "Diagnostics composite score",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>A single number in <strong>0–1</strong> summarizing multi-axis <strong>store health</strong>: retrieval signals, " +
            "freshness, completeness, duplication pressure, staleness/GC pressure, and integrity. It feeds the circuit breaker.</p>",
        },
        {
          heading: "The math",
          html:
            "<p><strong>Weighted sum</strong> of dimension scores, each also in <code>[0, 1]</code>:</p>" +
            "<p style='font-family:ui-monospace,monospace;font-size:0.85rem'>composite = clamp01( Σ<sub>i</sub> w<sub>i</sub> · score<sub>i</sub> )</p>" +
            "<p>Default built-in weights (re-normalized if you override in profile): " +
            "retrieval_effectiveness <strong>0.22</strong>, freshness <strong>0.18</strong>, completeness <strong>0.12</strong>, " +
            "duplication <strong>0.15</strong>, staleness <strong>0.15</strong>, integrity <strong>0.18</strong>.</p>" +
            "<p>Example dimension math (retrieval_effectiveness): intrinsic = <code>0.55×hit_rate + 0.45×mean_confidence</code> " +
            "(hit_rate = fraction of entries with access_count &gt; 0). If recall feedback events exist, they blend in.</p>" +
            "<p>Freshness uses exponential decay vs tier half-lives; duplication/staleness map candidate ratios into scores.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>One glance at “is this brain healthy?” without reading hundreds of memories. The scorecard maps composite to " +
            "<strong>ok / warn / fail</strong> using export thresholds (0.7 / 0.55), stricter than the breaker’s 0.6 / 0.3 bands.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>run_diagnostics()</code> evaluates each <code>HealthDimension</code>, then sums weighted scores. " +
            "Optional history can <strong>de-correlate</strong> weights when dimensions move together (Pearson &gt; 0.7).</p>" +
            "<p>Separately, an <strong>EWMA anomaly detector</strong> can flag when a dimension’s score drifts from its " +
            "smoothed baseline (λ=0.2, warn/crit z vs rolling variance, confirm window). That feeds recommendations—not this tile’s number.</p>",
        },
      ],
      reference: "Code: <code>run_diagnostics()</code>, <code>default_builtin_dimensions()</code> in <code>diagnostics.py</code>",
    },

    integrity_tampered: {
      title: "Integrity (tampered)",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>Entries whose stored <strong>integrity hash</strong> does not match a fresh hash over key, value, tier, and source. " +
            "Usually means the row was edited outside normal save paths or corrupted.</p>",
        },
        {
          heading: "The math",
          html:
            "<p>HMAC-SHA256-style digest over canonical fields (see <code>compute_integrity_hash</code>). " +
            "Verify recomputes and compares; mismatch increments tampered count.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Tampered rows break trust in audit and recall—content may not be what the system thought it saved.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>MemoryStore.verify_integrity()</code> walks entries with hashes and reports verified vs tampered counts and keys.</p>",
        },
      ],
      reference: "Code: <code>src/tapps_brain/integrity.py</code> · <code>store.verify_integrity()</code>",
    },

    integrity_no_hash: {
      title: "Integrity (missing hash)",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>Memories that have <strong>no</strong> <code>integrity_hash</code> yet (legacy imports, pre-migration rows, or paths that skipped hashing).</p>",
        },
        {
          heading: "The math",
          html: "<p>Count of entries with null/empty hash; not a probabilistic score.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>You cannot detect silent tampering on those rows until they are re-saved or backfilled.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p>New saves compute hashes on write. Diagnostics “integrity” dimension also reflects verified vs tampered among hashed rows.</p>",
        },
      ],
      reference: "Code: <code>persistence.py</code> / <code>store.save</code> integrity path",
    },

    store_capacity: {
      title: "Store capacity",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>How full the store is relative to <code>max_entries</code> (profile-driven, default 5,000).</p>",
        },
        {
          heading: "The math",
          html:
            "<p><code>ratio = entry_count / max_entries</code>. Scorecard: <strong>warn</strong> if ratio ≥ 0.8; " +
            "<strong>fail</strong> if ratio ≥ 0.95 (visual snapshot thresholds).</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Near the cap, new saves may be rejected or require GC/archival policy changes.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p>Enforces max entries in <code>MemoryStore.save</code>; health exposes counts for monitoring.</p>",
        },
      ],
      reference: "Code: <code>MemoryStore</code> · <code>_MAX_ENTRIES</code> / profile limits",
    },

    rate_limits: {
      title: "Rate limit anomalies",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>Counts from the store’s <strong>sliding-window rate limiter</strong> when writes burst harder than configured limits—" +
            "minute- and session-scoped anomaly tallies.</p>",
        },
        {
          heading: "The math",
          html:
            "<p>Not shown as a formula in the JSON; the export surfaces integer counters incremented when limits trip. " +
            "Zero means no anomalies recorded in current process/metrics view.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Spikes often mean runaway agent loops, bad integration, or mis-sized limits—worth investigating before users see failed saves.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>SlidingWindowRateLimiter</code> tracks writes; health copies anomaly counters into the snapshot.</p>",
        },
      ],
      reference: "Code: <code>rate_limiter.py</code> · <code>StoreHealthReport</code> rate limit fields",
    },

    maintenance_backlog: {
      title: "Maintenance backlog",
      sections: [
        {
          heading: "What it is",
          html:
            "<p><strong>GC candidates</strong> (stale/archivable) plus <strong>consolidation candidates</strong> (mergeable similar entries) " +
            "reported by heuristics—not yet executed.</p>",
        },
        {
          heading: "The math",
          html:
            "<p><code>backlog = gc_candidates + consolidation_candidates</code>. Scorecard warns if backlog &gt; <strong>200</strong>; " +
            "otherwise info if &gt; 0, else ok.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Large backlogs mean more duplicate noise or stale facts until maintenance runs.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p>Deterministic similarity/consolidation and GC discovery (no LLM). Operators run maintenance commands when ready.</p>",
        },
      ],
      reference: "Code: <code>consolidation.py</code>, <code>gc.py</code>, <code>store.health()</code>",
    },

    hive_hub: {
      title: "Hive hub reachability",
      sections: [
        {
          heading: "What it is",
          html:
            "<p><strong>Hive</strong> is the shared cross-agent store under <code>~/.tapps-brain/hive/</code>. " +
            "This check contrasts: (a) whether <em>this</em> project store injects a Hive client, vs (b) whether an export could " +
            "open the hub and read namespace/agent stats.</p>",
        },
        {
          heading: "The math",
          html: "<p>No numeric formula—connectivity boolean plus counts of agents and shared entries when connected.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>If you expect multi-agent propagation but the hub is down or empty, recalls stay local-only.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>HiveStore</code> + <code>AgentRegistry</code>; propagation engine routes <code>agent_scope</code> " +
            "(private / domain / hive / group:…).</p>",
        },
      ],
      reference: "Docs: <code>docs/guides/hive.md</code> · Code: <code>hive.py</code>",
    },

    retrieval_stack: {
      title: "Retrieval stack",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>Which <strong>lexical + vector</strong> path is effectively active: BM25-only, hybrid with sqlite-vec KNN, " +
            "hybrid with empty vec index, or on-the-fly embeddings without sqlite-vec.</p>",
        },
        {
          heading: "The math",
          html:
            "<p>BM25 uses Okapi-style scoring over tokens; hybrid fuses BM25 ranks with vector ranks (e.g. RRF). " +
            "sqlite-vec stores embeddings for KNN in-process. Mode is derived from installed extras + extension + row counts—" +
            "no neural model is loaded for this health signal.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Semantic recall quality and latency depend on this stack; BM25-only is fine for keyword-heavy use, weak for paraphrase.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>retrieval_health_slice(store)</code> mirrors CLI/MCP retrieval health; <code>MemoryRetriever</code> runs the actual hybrid pipeline.</p>",
        },
      ],
      reference: "Code: <code>health_check.py</code> · <code>retrieval.py</code> · <code>fusion.py</code>",
    },

    sqlcipher: {
      title: "SQLCipher",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>SQLite database file encryption at rest (optional build). When on, the memory DB requires a key to open.</p>",
        },
        {
          heading: "The math",
          html: "<p>N/A (crypto provided by SQLCipher extension).</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Compliance and laptop-loss scenarios; slightly different ops (key management, backups).</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p>Health exposes <code>sqlcipher_enabled</code>; connections go through <code>sqlcipher_util</code>.</p>",
        },
      ],
      reference: "Code: <code>sqlcipher_util.py</code> · issue #23 docs",
    },
  };

  const HELP_CONCEPTS = {
    live_connection_status: {
      title: "Live connection status badge",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>The <strong>status badge</strong> in the top-right header shows whether the dashboard is receiving " +
            "live data from the tapps-brain <code>/snapshot</code> HTTP endpoint.</p>" +
            "<ul>" +
            "<li><strong style='color:#15803d'>LIVE</strong> — last successful fetch within 90 seconds; timestamp shown.</li>" +
            "<li><strong style='color:#b45309'>STALE</strong> — last fetch was more than 90 seconds ago, or no fetch yet (OFFLINE).</li>" +
            "<li><strong style='color:#b91c1c'>ERROR</strong> — 3 consecutive fetch failures; error message shown.</li>" +
            "</ul>",
        },
        {
          heading: "The polling loop",
          html:
            "<p>The dashboard calls <code>fetch('/snapshot', { cache: 'no-store' })</code> on page load and " +
            "then on the selected interval (default 30 s). Use the <strong>Refresh</strong> selector in the header " +
            "to change the cadence to 15 s, 60 s, or Manual (fetch once, no auto-refresh).</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Without live polling the dashboard shows stale exported JSON. With the HttpAdapter running, " +
            "every panel updates automatically — no <code>tapps-brain visual export</code> needed.</p>",
        },
        {
          heading: "Setup",
          html:
            "<p>Start the HttpAdapter: <code>tapps-brain mcp start --http</code> or " +
            "<code>docker compose up tapps-brain-mcp</code>. When running the visual dashboard in Docker, " +
            "nginx proxies <code>/snapshot</code> to <code>http://tapps-brain-mcp:8080/snapshot</code> " +
            "so the browser fetch is same-origin (no CORS issues).</p>",
        },
      ],
      reference: "Code: <code>examples/brain-visual/index.html</code> · <code>initLivePolling()</code> · STORY-065.2",
    },

    fingerprint: {
      title: "Fingerprint (SHA-256)",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>A <strong>64-character hex</strong> hash of a small JSON object describing store <em>identity</em>: entry count, " +
            "tier mix, agent_scope counts, profile, DB schema version, store path (redacted in strict privacy), Hive attached, " +
            "federation flag, memory_group count.</p>",
        },
        {
          heading: "The math",
          html:
            "<p><code>SHA-256( UTF-8( canonical_json(identity) ) )</code> with sorted keys and compact separators. " +
            "Same identity object → same fingerprint.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Compare exports/screenshots without sharing memory text. Theme accents on this page are seeded from the same hash for a stable look.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>compute_fingerprint_hex</code> in <code>visual_snapshot.py</code>; not the same as per-entry integrity hashes.</p>",
        },
      ],
      reference: "Code: <code>visual_snapshot.py</code> · <code>identity_schema_version</code>",
    },

    snapshot_json: {
      title: "Snapshot JSON (brain-visual.json)",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>A <strong>versioned</strong> export of <em>aggregated</em> metadata for dashboards. Schema version 2 adds retrieval, " +
            "Hive hub slice, access histograms, memory groups, scorecard, and optional tag stats (local privacy only).</p>",
        },
        {
          heading: "The math",
          html:
            "<p>No ML. Counts, ratios, and deterministic scorecard rules only.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Lets you render ops views and file tickets without leaking memory bodies or keys in the artifact (unless you choose local tier for tags/groups).</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>tapps-brain visual export</code> → <code>build_visual_snapshot()</code>.</p>",
        },
      ],
      reference: "Docs: <code>docs/guides/visual-snapshot.md</code>",
    },

    privacy_tiers: {
      title: "Privacy tiers (export)",
      sections: [
        {
          heading: "What it is",
          html:
            "<p><strong>standard</strong> — path + aggregates; no tag names or per-group names in JSON. " +
            "<strong>strict</strong> — redacts <code>store_path</code> and tampered key list. " +
            "<strong>local</strong> — adds tag frequencies and <code>memory_group</code> name→count map (do not post publicly).</p>",
        },
        {
          heading: "The math",
          html: "<p>N/A — policy filters on fields.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Tags and group names leak <em>vocabulary</em> about the project; strict protects paths in shared screenshots.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>--privacy</code> on <code>visual export</code>; fingerprint identity uses redacted path when strict.</p>",
        },
      ],
      reference: "Code: <code>visual_snapshot.py</code> · <code>PrivacyTier</code>",
    },

    tier_distribution: {
      title: "Tier distribution",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>How many memories sit in each <strong>tier</strong> (architectural, pattern, procedural, context, …). Tiers drive " +
            "decay half-lives and retrieval priors.</p>",
        },
        {
          heading: "The math",
          html:
            "<p>Per-tier counts; bar chart is proportional to max tier count on this page.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>All-architectural vs all-context profiles behave differently in time and consolidation.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>MemoryTier</code> on each entry; decay in <code>decay.py</code>.</p>",
        },
      ],
      reference: "Code: <code>models.py</code> · <code>decay.py</code>",
    },

    access_histogram: {
      title: "Access histogram",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>Buckets of how many memories fall into each <code>access_count</code> range (0, 1–5, 6–20, 21+). " +
            "Also sums <code>total_access_count</code> / <code>useful_access_count</code> when present.</p>",
        },
        {
          heading: "The math",
          html:
            "<p>Per entry, read <code>access_count</code>; increment one bucket. Means and sums are arithmetic aggregates—no smoothing.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Shows whether the store is “cold” (nothing recalled) or a few hot notes dominate frequency scoring.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p>Retrieval bumps access counters; composite retrieval_effectiveness uses hit_rate partly from access_count &gt; 0.</p>",
        },
      ],
      reference: "Code: <code>visual_snapshot.py</code> · <code>_access_stats_from_entries</code>",
    },

    agent_scope: {
      title: "Agent scope (private / domain / hive / group)",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>Per-memory <strong>propagation class</strong>: stay private, share in domain, push to Hive, or partition by " +
            "<code>group:name</code> for Hive namespaces.</p>",
        },
        {
          heading: "The math",
          html: "<p>Counts per scope string in this export.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Wrong scope leaks data to other agents or hides team-shared facts.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>PropagationEngine</code> routes saves; recall merges local + Hive with weights.</p>",
        },
      ],
      reference: "Docs: <code>docs/guides/memory-scopes.md</code> · <code>hive.md</code>",
    },

    /* memory_group: archived — section removed from dashboard (STORY-065.3, privacy-gated) */

    integrity_panel: {
      title: "Integrity & rate limits (panel)",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>This block summarizes <strong>relation graph size</strong>, <strong>HMAC integrity</strong> outcomes, " +
            "rate-limit anomaly counters, GC/consolidation candidates, and optional save-phase latency text from in-process metrics.</p>",
        },
        {
          heading: "The math",
          html:
            "<p><strong>Tampered</strong> / <strong>verified</strong> / <strong>no hash</strong> are counts from " +
            "<code>verify_integrity()</code>. Rate-limit fields are integer anomaly counters. Relations count is graph edge/row scale " +
            "(see store API).</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Together with the scorecard’s integrity rows, you can tell if the store is structurally sound before trusting recall for audits.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p>Integrity hashes protect key/value/tier/source tuples; relations link memories; rate limiter protects against runaway writes.</p>",
        },
      ],
      reference: "Code: <code>store.verify_integrity()</code> · <code>relations</code> · <code>metrics.py</code>",
    },

    /* tags_panel: archived — section removed from dashboard (STORY-065.3, privacy-gated) */

    privacy_notice: {
      title: "Privacy (this page)",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>The footer and export notice describe which fields are excluded from <code>brain-visual.json</code> by design " +
            "(no raw memory values, no keys in normal tiers).</p>",
        },
        {
          heading: "The math",
          html: "<p>N/A.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Safe to drop JSON into tickets after <code>--privacy strict</code>; local tier needs care.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p>Enforced in <code>build_visual_snapshot</code>; documented in <code>visual-snapshot.md</code>.</p>",
        },
      ],
      reference: "Docs: <code>docs/guides/visual-snapshot.md</code>",
    },

    kpi_strip: {
      title: "Top KPI strip",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>The five boxes under the hero row: <strong>Entries</strong> (count vs max), <strong>DB schema</strong> migration " +
            "version, <strong>Privacy</strong> export tier (v2), <strong>Hive hub</strong> reachability + shared entry count, " +
            "and <strong>Tiers (rows)</strong> — sum of tier_distribution counts.</p>",
        },
        {
          heading: "The math",
          html:
            "<p>All values come straight from <code>health</code> and v2 snapshot fields; tier sum is arithmetic over " +
            "<code>tier_distribution</code> values.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Skimmable at page load before you scroll to sections; should match the bento tiles and Pulse chart.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p>Populated in the demo from loaded JSON; exporter is <code>build_visual_snapshot()</code>.</p>",
        },
      ],
      reference: "Code: <code>visual_snapshot.py</code> · demo <code>index.html</code> KPI block",
    },

    scorecard_counts: {
      title: "Scorecard summary counts",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>Four KPIs: <strong>Pass</strong> (status ok), <strong>Attention</strong> (warn + unknown), " +
            "<strong>Blocked</strong> (fail), <strong>Info</strong> (informational rows).</p>",
        },
        {
          heading: "The math",
          html:
            "<p>Each scorecard row has exactly one status; the demo counts them after merge of embedded <code>scorecard[]</code> " +
            "or browser-derived rules.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Triages how many checks need human follow-up vs are clean, before reading every card.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p>Python emits authoritative rows; <code>scorecard-derive.js</code> reproduces them for older JSON files.</p>",
        },
      ],
      reference: "Code: <code>visual_snapshot._build_scorecard</code> · <code>scorecard-derive.js</code>",
    },

    issue_ticket_draft: {
      title: "Issue / ticket draft",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>Free-text notes plus one-click <strong>Copy GitHub issue (Markdown)</strong> or <strong>Copy plain summary</strong>. " +
            "The clipboard body includes fingerprint, package/DB/snapshot versions, failing and attention rows, and full table.</p>",
        },
        {
          heading: "The math",
          html: "<p>No scoring — template assembly only.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Turns the deterministic scorecard into a paste-ready ops ticket without retyping metrics.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p>Client-side only in <code>index.html</code>; data never leaves your browser except when you paste elsewhere.</p>",
        },
      ],
      reference: "Demo: <code>buildIssueMarkdown</code> / <code>buildPlainSummary</code> in <code>index.html</code>",
    },

    memory_profile: {
      title: "Memory profile (YAML)",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>The <strong>named profile</strong> loaded for this store (e.g. limits, decay, hybrid fusion, diagnostics retention). " +
            "Shown under the entry count as <code>(default profile)</code> when none is set.</p>",
        },
        {
          heading: "The math",
          html: "<p>N/A — configuration label from store health.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Same machine can behave differently with different profiles; compare fingerprints only when profile matches intent.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>MemoryProfile</code> from YAML; <code>health().profile_name</code> surfaces in the snapshot.</p>",
        },
      ],
      reference:
        "Code: <code>src/tapps_brain/profile.py</code> (<code>MemoryProfile</code>) · " +
        "<code>StoreHealthReport.profile_name</code> in <code>metrics.py</code>",
    },

    federation_snapshot: {
      title: "Federation (export flag)",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>Whether <strong>cross-project federation</strong> is enabled for this store — the “federation on/off” line under DB schema. " +
            "Separate from Hive (agent shared store).</p>",
        },
        {
          heading: "The math",
          html: "<p>Boolean from <code>health.federation_enabled</code>.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>Federated hubs pull/push memories across projects; operators need to know if this instance participates.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>federation.py</code> hub at <code>~/.tapps-brain/memory/federated.db</code>; optional opt-in per store.</p>",
        },
      ],
      reference:
        "Docs: <code>docs/guides/hive-vs-federation.md</code> · <code>docs/engineering/system-architecture.md</code> · " +
        "<code>src/tapps_brain/federation.py</code>",
    },

    scorecard_overview: {
      title: "Scorecard (how to read it)",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>Deterministic <strong>pass / warn / fail / info / unknown</strong> rows derived from the same numbers you see elsewhere " +
            "on this page. It is a triage aid, not a second opinion from an LLM.</p>",
        },
        {
          heading: "The math",
          html:
            "<p>Each row applies fixed thresholds (documented in <code>visual_snapshot._build_scorecard</code>). " +
            "The dashboard’s green/amber/red uses the same data as the GitHub issue template.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>On-call can answer “what’s wrong?” from one JSON + one page, then open a ticket with **Copy GitHub issue**.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p>Python builds <code>scorecard[]</code>; the browser can re-derive from older files via <code>scorecard-derive.js</code>.</p>",
        },
      ],
      reference: "Code: <code>visual_snapshot.py</code> · <code>ScorecardCheck</code>",
    },

    retrieval_pipeline: {
      title: "Retrieval pipeline (how it works)",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>tapps-brain uses a <strong>two-stage retrieval pipeline</strong>: lexical BM25 scoring and, when " +
            "pgvector is available, semantic KNN search. The pipeline is deterministic — no LLM is called for recall.</p>" +
            "<ol><li><strong>BM25</strong> — Okapi BM25 scores tokens in memory entries against your query; fast and exact.</li>" +
            "<li><strong>pgvector KNN</strong> — Cosine-nearest-neighbour over stored embeddings (if any vectors are present).</li>" +
            "<li><strong>RRF fusion</strong> — Reciprocal Rank Fusion merges BM25 ranks and vector ranks into a single " +
            "ranked list (no direct score comparison needed).</li></ol>",
        },
        {
          heading: "The math",
          html:
            "<p><strong>BM25:</strong> <code>score = IDF × (tf × (k1 + 1)) / (tf + k1 × (1 − b + b × dl / avgdl))</code> " +
            "where k1=1.5, b=0.75 (Okapi defaults).</p>" +
            "<p><strong>RRF:</strong> <code>RRFscore(d) = Σ 1 / (k + rank_i(d))</code> where k=60 by default. " +
            "Equal weight per ranked list; biased toward entries that appear near the top in both lists.</p>" +
            "<p><strong>Composite retrieval weights</strong> (post-fusion): relevance 40%, confidence 30%, recency 15%, " +
            "frequency 15% (configurable in profile).</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>BM25-only recall is strong for exact or keyword-heavy queries. Hybrid (BM25 + vector) adds " +
            "semantic coverage for paraphrased or conceptually similar queries. The effective mode shown in this " +
            "dashboard tells you which path your brain is using.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>MemoryRetriever</code> in <code>retrieval.py</code> orchestrates the pipeline. " +
            "<code>bm25.py</code> provides pure-Python Okapi BM25. " +
            "<code>fusion.py</code> implements RRF. <code>retrieval_health_slice()</code> in <code>health_check.py</code> " +
            "determines the effective mode from installed extras + pgvector extension presence + vector row count.</p>",
        },
      ],
      reference:
        "Code: <code>retrieval.py</code> · <code>bm25.py</code> · <code>fusion.py</code> · " +
        "<code>health_check.py: retrieval_health_slice</code>",
    },

    composite_score: {
      title: "Composite health score",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>A single <strong>0 – 1 number</strong> summarising the current quality of the tapps-brain memory store. " +
            "It is a weighted average of four dimensions measured across all stored entries.</p>" +
            "<ul><li><strong>Relevance (40%)</strong> — are recalled entries topically matched?</li>" +
            "<li><strong>Confidence (30%)</strong> — average confidence score across active entries.</li>" +
            "<li><strong>Recency (15%)</strong> — recency-weighted access; stale entries drag this down.</li>" +
            "<li><strong>Frequency (15%)</strong> — how often entries are accessed; rarely-touched entries " +
            "contribute less.</li></ul>",
        },
        {
          heading: "The math",
          html:
            "<p><code>composite = 0.4 × relevance + 0.3 × confidence + 0.15 × recency + 0.15 × frequency</code></p>" +
            "<p>All component scores are normalised 0 – 1. The composite drives circuit-breaker transitions " +
            "at <strong>0.6</strong> (closed ↔ half-open) and <strong>0.3</strong> (half-open ↔ open).</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>A falling composite score is an early warning that recall quality is degrading — before agents " +
            "notice confusing or stale memories. Score ≥ 0.6 means the circuit is CLOSED and fully trusted; " +
            "0.3 – 0.6 is the degraded band; below 0.3 the circuit opens and recalls are flagged.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>diagnostics.py</code> computes the scorecard using EWMA anomaly detection and the circuit-breaker " +
            "state machine. <code>RecallResult.quality_warning</code> is set when circuit is not CLOSED, " +
            "so agents can react.</p>",
        },
      ],
      reference:
        "Code: <code>diagnostics.py</code> · <code>models.py: RecallResult.quality_warning</code> · " +
        "EPIC-030 (diagnostic scorecard design)",
    },

    circuit_breaker: {
      title: "Circuit breaker (health state machine)",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>tapps-brain uses a three-state <strong>circuit breaker</strong> pattern (borrowed from distributed systems) " +
            "to track and communicate recall quality — without any LLM call.</p>" +
            "<ul><li><strong>CLOSED</strong> — healthy; composite score ≥ 0.6. Full recall, no warning.</li>" +
            "<li><strong>HALF-OPEN</strong> — probing; previous circuit-open cooldown elapsed. " +
            "Probe recalls run; if they pass, circuit closes again.</li>" +
            "<li><strong>OPEN</strong> — degraded; composite score &lt; 0.3. Recalls still work but " +
            "<code>RecallResult.quality_warning</code> is set so agents know to treat results with caution.</li></ul>",
        },
        {
          heading: "The math",
          html:
            "<p>State transitions use EWMA (Exponentially Weighted Moving Average) over diagnostic scores with " +
            "configurable thresholds (default 0.6 / 0.3). Cooldown period before OPEN → HALF-OPEN transition " +
            "prevents flapping. All transitions are deterministic — no randomness.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>An OPEN circuit is a safe-fail signal, not a hard stop. Agents can degrade gracefully " +
            "(e.g. prompt the user to run <code>tapps-brain gc</code> or <code>consolidation</code>) " +
            "rather than silently returning stale results.</p>",
        },
        {
          heading: "What tapps-brain does",
          html:
            "<p><code>DiagnosticsEngine.circuit_state</code> (in <code>diagnostics.py</code>) drives the state " +
            "machine. <code>MemoryStore.recall()</code> propagates the warning through <code>RecallResult</code>. " +
            "<code>build_visual_snapshot()</code> exports both composite score and circuit state.</p>",
        },
      ],
      reference:
        "Code: <code>diagnostics.py</code> · <code>models.py: RecallResult</code> · " +
        "Scorecard check: <em>Diagnostics circuit</em>",
    },

    hive_namespace_detail: {
      title: "Hive namespace detail table",
      sections: [
        {
          heading: "What it is",
          html:
            "<p>The <strong>namespace detail table</strong> in the Hive hub panel shows one row per namespace " +
            "currently stored in the shared Hive database. Each row shows the namespace name, its total entry count, " +
            "and a relative timestamp of the most recent write (<em>e.g.</em> '5m ago', '2h ago').</p>",
        },
        {
          heading: "How it is collected",
          html:
            "<p>A single <code>SELECT namespace, COUNT(*), COALESCE(MAX(updated_at), MAX(created_at)) " +
            "FROM hive_memories GROUP BY namespace</code> query is run against the Postgres Hive at snapshot " +
            "time. This avoids one query per namespace and keeps the snapshot endpoint fast.</p>",
        },
        {
          heading: "Status badges",
          html:
            "<ul>" +
            "<li><strong style='color:#047857'>● Connected</strong> — Hive is reachable and the table reflects live data.</li>" +
            "<li><strong style='color:#b45309'>⚠ Degraded</strong> — Hive returned an error but was previously reachable.</li>" +
            "<li><strong style='color:#b91c1c'>● Offline</strong> — Hive is not reachable from this host or DSN is unset.</li>" +
            "</ul>",
        },
        {
          heading: "Empty state",
          html:
            "<p>When the Hive is connected but no namespaces exist yet (fresh deployment), the table shows " +
            "<em>No namespaces — Hive has no data yet.</em> This is normal on first startup.</p>",
        },
        {
          heading: "Why it matters",
          html:
            "<p>A namespace that stopped receiving writes, or one that is growing unexpectedly large, " +
            "is invisible in a single-line prose string. The table makes per-namespace growth and " +
            "staleness immediately obvious without running <code>psql</code>.</p>",
        },
      ],
      reference:
        "Code: <code>visual_snapshot.py: HiveHealthSummary.namespace_detail</code> · " +
        "<code>postgres_hive.py: namespace_detail_list()</code> · STORY-065.4",
    },
  };

  function openBrainVisualHelp(type, id) {
    const map = type === "concept" ? HELP_CONCEPTS : HELP_SCORECARD;
    const entry = map[id];
    const drawer = document.getElementById("help-drawer");
    const titleEl = document.getElementById("help-drawer-title");
    const bodyEl = document.getElementById("help-drawer-body");
    const backdrop = document.getElementById("help-backdrop");
    if (!drawer || !titleEl || !bodyEl) return;

    if (!entry) {
      titleEl.textContent = "Help";
      bodyEl.innerHTML = "<p>No detailed article is linked for this item yet.</p>";
    } else {
      titleEl.textContent = entry.title;
      let html = "";
      for (const s of entry.sections || []) {
        html += "<h4 class=\"help-drawer-h4\">" + s.heading + "</h4>";
        html += "<div class=\"help-drawer-section\">" + s.html + "</div>";
      }
      if (entry.reference) {
        html += "<p class=\"help-drawer-ref\">" + entry.reference + "</p>";
      }
      bodyEl.innerHTML = html;
    }

    drawer.classList.add("is-open");
    drawer.setAttribute("aria-hidden", "false");
    if (backdrop) {
      backdrop.hidden = false;
      backdrop.classList.add("is-on");
    }
    document.body.classList.add("help-drawer-open");
    const closeBtn = document.getElementById("help-drawer-close");
    if (closeBtn) closeBtn.focus();
  }

  function closeBrainVisualHelp() {
    const drawer = document.getElementById("help-drawer");
    const backdrop = document.getElementById("help-backdrop");
    if (drawer) {
      drawer.classList.remove("is-open");
      drawer.setAttribute("aria-hidden", "true");
    }
    if (backdrop) {
      backdrop.hidden = true;
      backdrop.classList.remove("is-on");
    }
    document.body.classList.remove("help-drawer-open");
  }

  window.BRAIN_VISUAL_HELP = {
    open: openBrainVisualHelp,
    close: closeBrainVisualHelp,
    scorecardIds: Object.keys(HELP_SCORECARD),
    conceptIds: Object.keys(HELP_CONCEPTS),
  };

  function bindHelpChrome() {
    const backdrop = document.getElementById("help-backdrop");
    const closeBtn = document.getElementById("help-drawer-close");
    if (closeBtn) closeBtn.addEventListener("click", closeBrainVisualHelp);
    if (backdrop) backdrop.addEventListener("click", closeBrainVisualHelp);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeBrainVisualHelp();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindHelpChrome);
  } else {
    bindHelpChrome();
  }
})();
