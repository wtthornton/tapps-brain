/**
 * Client-side scorecard when JSON lacks `scorecard[]` (older exports).
 * Mirrors tapps_brain.visual_snapshot._build_scorecard — keep in sync on rule changes.
 */
function deriveScorecardFromSnapshot(data) {
  if (Array.isArray(data.scorecard) && data.scorecard.length) return data.scorecard;

  const health = data.health && typeof data.health === "object" ? data.health : {};
  const hh = data.hive_health && typeof data.hive_health === "object" ? data.hive_health : null;
  const hiveAttached = !!data.hive_attached;
  const mode =
    typeof data.retrieval_effective_mode === "string" ? data.retrieval_effective_mode : "unknown";
  const diag = data.diagnostics && typeof data.diagnostics === "object" ? data.diagnostics : null;
  const checks = [];

  const entryCount = Number(health.entry_count) || 0;
  const maxEntries = Number(health.max_entries) || 5000;

  checks.push({
    id: "store_entries",
    title: "Store contents",
    status: entryCount ? "ok" : "info",
    detail: entryCount
      ? `${entryCount} memor(y/ies) within max ${maxEntries}.`
      : "No memories in this project store yet.",
    ticket_hint: "",
  });

  if (!diag) {
    checks.push({
      id: "diagnostics_data",
      title: "Diagnostics data",
      status: "unknown",
      detail: "Diagnostics omitted or missing from file.",
      ticket_hint: "Re-export without --skip-diagnostics for circuit/score signals.",
    });
  } else {
    checks.push({
      id: "diagnostics_data",
      title: "Diagnostics data",
      status: "ok",
      detail: "Composite score and circuit state included in this export.",
      ticket_hint: "",
    });
    const circuit = String(diag.circuit_state || "").toLowerCase();
    let cstat;
    let cdetail;
    if (circuit === "closed") {
      cstat = "ok";
      cdetail = "Diagnostics circuit is closed (nominal).";
    } else if (circuit === "degraded" || circuit === "half_open") {
      cstat = "warn";
      cdetail = `Circuit state: ${diag.circuit_state}.`;
    } else if (circuit === "open") {
      cstat = "fail";
      cdetail = `Circuit state: ${diag.circuit_state}.`;
    } else {
      cstat = "warn";
      cdetail = `Unknown circuit state: ${diag.circuit_state}.`;
    }
    checks.push({
      id: "diagnostics_circuit",
      title: "Diagnostics circuit",
      status: cstat,
      detail: cdetail,
      ticket_hint: "Run `tapps-brain diagnostics health` and review scorecard dimensions.",
    });
    const score = Number(diag.composite_score);
    let sstat;
    if (score >= 0.7) sstat = "ok";
    else if (score >= 0.55) sstat = "warn";
    else sstat = "fail";
    checks.push({
      id: "diagnostics_composite",
      title: "Diagnostics composite score",
      status: sstat,
      detail: `Composite score ${score.toFixed(2)} (0-1).`,
      ticket_hint: "Inspect diagnostics history and recall quality signals.",
    });
  }

  const tampered = Number(health.integrity_tampered) || 0;
  if (tampered === 0) {
    checks.push({
      id: "integrity_tampered",
      title: "Integrity (tampered)",
      status: "ok",
      detail: "No tampered integrity hashes reported.",
      ticket_hint: "",
    });
  } else {
    checks.push({
      id: "integrity_tampered",
      title: "Integrity (tampered)",
      status: "fail",
      detail: `${tampered} entr(y/ies) failed integrity verification.`,
      ticket_hint: "Run store maintenance / verify_integrity; do not ignore on shared stores.",
    });
  }

  const noHash = Number(health.integrity_no_hash) || 0;
  if (noHash > 0) {
    checks.push({
      id: "integrity_no_hash",
      title: "Integrity (missing hash)",
      status: "warn",
      detail: `${noHash} entr(y/ies) have no integrity hash (legacy or pending backfill).`,
      ticket_hint: "Consider re-saving or running migration path if hashes are expected.",
    });
  } else {
    checks.push({
      id: "integrity_no_hash",
      title: "Integrity (missing hash)",
      status: "ok",
      detail: "No entries missing integrity hashes.",
      ticket_hint: "",
    });
  }

  if (maxEntries > 0) {
    const ratio = entryCount / maxEntries;
    let capStat;
    let capDetail;
    if (ratio >= 0.95) {
      capStat = "fail";
      capDetail = `Store at ${Math.round(ratio * 100)}% of max_entries (${entryCount}/${maxEntries}).`;
    } else if (ratio >= 0.8) {
      capStat = "warn";
      capDetail = `Store at ${Math.round(ratio * 100)}% of max_entries (${entryCount}/${maxEntries}).`;
    } else {
      capStat = "ok";
      capDetail = `Capacity ${Math.round(ratio * 100)}% of max_entries (${entryCount}/${maxEntries}).`;
    }
    checks.push({
      id: "store_capacity",
      title: "Store capacity",
      status: capStat,
      detail: capDetail,
      ticket_hint: "Raise max_entries in profile or archive/GC if appropriate.",
    });
  }

  const rma = Number(health.rate_limit_minute_anomalies) || 0;
  const rsa = Number(health.rate_limit_session_anomalies) || 0;
  if (rma === 0 && rsa === 0) {
    checks.push({
      id: "rate_limits",
      title: "Rate limit anomalies",
      status: "ok",
      detail: "No minute/session rate-limit anomalies recorded.",
      ticket_hint: "",
    });
  } else {
    checks.push({
      id: "rate_limits",
      title: "Rate limit anomalies",
      status: "warn",
      detail: `Minute anomalies: ${rma}; session anomalies: ${rsa}.`,
      ticket_hint: "Review burst writes and profile rate_limit settings.",
    });
  }

  const gcC = Number(health.gc_candidates) || 0;
  const consC = Number(health.consolidation_candidates) || 0;
  const backlog = gcC + consC;
  let mbStat;
  let mbDetail;
  if (backlog > 200) {
    mbStat = "warn";
    mbDetail = `Maintenance backlog: ${gcC} GC + ${consC} consolidation candidate(s).`;
  } else if (backlog > 0) {
    mbStat = "info";
    mbDetail = `Some maintenance candidates: ${gcC} GC, ${consC} consolidation.`;
  } else {
    mbStat = "ok";
    mbDetail = "No GC or consolidation candidates reported.";
  }
  checks.push({
    id: "maintenance_backlog",
    title: "Maintenance backlog",
    status: mbStat,
    detail: mbDetail,
    ticket_hint: "Run GC / consolidation when maintenance windows allow.",
  });

  if (hiveAttached && hh && !hh.connected) {
    checks.push({
      id: "hive_hub",
      title: "Hive hub reachability",
      status: "warn",
      detail: "Store has Hive injection but hub snapshot could not connect.",
      ticket_hint: "Check ~/.tapps-brain/hive/, AgentRegistry, and Hive CLI health.",
    });
  } else if (hiveAttached && hh && hh.connected) {
    const agents = Number(hh.agents) || 0;
    const hent = Number(hh.entries) || 0;
    if (agents === 0) {
      checks.push({
        id: "hive_hub",
        title: "Hive hub reachability",
        status: "warn",
        detail: "Hub connected but no agents registered.",
        ticket_hint: "Register agents via `tapps-brain agent register` or equivalent.",
      });
    } else {
      checks.push({
        id: "hive_hub",
        title: "Hive hub reachability",
        status: "ok",
        detail: `Hub connected; ${agents} agent(s), ${hent} shared entr(y/ies).`,
        ticket_hint: "",
      });
    }
  } else {
    checks.push({
      id: "hive_hub",
      title: "Hive hub reachability",
      status: "info",
      detail: "Hive not injected on this store (local-only mode) or hub not queried.",
      ticket_hint: "",
    });
  }

  let rstat;
  let rdetail;
  if (mode === "hybrid_sqlite_vec_knn") {
    rstat = "ok";
    rdetail = "Hybrid BM25 + sqlite-vec KNN active.";
  } else if (mode === "bm25_only") {
    rstat = "info";
    rdetail = "BM25-only retrieval (vector stack unavailable or empty).";
  } else if (mode === "hybrid_sqlite_vec_empty") {
    rstat = "warn";
    rdetail = "sqlite-vec on but vector index empty; embeddings may run on the fly.";
  } else if (mode === "hybrid_on_the_fly_embeddings") {
    rstat = "info";
    rdetail = "Hybrid without sqlite-vec KNN; vectors computed on demand.";
  } else if (mode === "unknown") {
    rstat = "warn";
    rdetail = "Could not classify retrieval mode.";
  } else {
    rstat = "info";
    rdetail = `Retrieval mode: ${mode}.`;
  }
  checks.push({
    id: "retrieval_stack",
    title: "Retrieval stack",
    status: rstat,
    detail: rdetail,
    ticket_hint: "Align with `uv sync --extra vector` and sqlite-vec docs if hybrid expected.",
  });

  if (health.sqlcipher_enabled) {
    checks.push({
      id: "sqlcipher",
      title: "SQLCipher",
      status: "info",
      detail: "Encrypted SQLite (SQLCipher) enabled for this store.",
      ticket_hint: "",
    });
  }

  return checks;
}
