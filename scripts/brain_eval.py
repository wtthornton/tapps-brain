#!/usr/bin/env python3
"""Operational evaluation of a *running* tapps-brain stack over a time window.

Collects everything needed to judge how well the deployed brain is working and
where it can improve, from four sources, each with a different true coverage:

1. Docker stack status   -- container image/uptime/health/restarts (live).
2. HTTP control plane     -- /healthz?deep=1, /ready, /metrics, /snapshot.
                             ``/metrics`` counters are *in-memory*: they only
                             cover time since the last process start, NOT the
                             full window. The report labels this explicitly.
3. Postgres analytics     -- persistent tables (private_memories, experience_
                             events, feedback_events, audit_log, diagnostics_
                             history, kg_*, hive_memories, gc_archive, ...).
                             These cover the FULL window (and beyond).
4. Container logs         -- error/warning/traceback extraction. Coverage is the
                             current container's log retention (often < window
                             if the container was recreated).

Designed to be run repeatedly. Writes a timestamped Markdown report plus a JSON
artifact under ``OUT_DIR`` and prints a console summary.

Usage:
    python3 scripts/brain_eval.py                 # default 72h window
    WINDOW_HOURS=24 python3 scripts/brain_eval.py
    make brain-eval                               # if wired in the Makefile

Environment overrides:
    WINDOW_HOURS              default 72
    TAPPS_BRAIN_HTTP_CONTAINER default tapps-brain-http
    TAPPS_BRAIN_DB_CONTAINER   default tapps-brain-db
    TAPPS_BRAIN_DB_USER        default tapps
    TAPPS_BRAIN_DB_NAME        default tapps_brain
    TAPPS_BRAIN_VISUAL_CONTAINER default tapps-visual
    TAPPS_BRAIN_BASE_URL       default http://127.0.0.1:8080
    TAPPS_BRAIN_VISUAL_URL     default http://127.0.0.1:8088
    TAPPS_BRAIN_AUTH_TOKEN     read from env, else docker/.env
    TAPPS_BRAIN_METRICS_TOKEN  read from env, else docker/.env
    OUT_DIR                    default <repo>/brain-eval-reports
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

WINDOW_HOURS = int(os.environ.get("WINDOW_HOURS", "72") or "72")
HTTP_CONTAINER = os.environ.get("TAPPS_BRAIN_HTTP_CONTAINER", "tapps-brain-http")
DB_CONTAINER = os.environ.get("TAPPS_BRAIN_DB_CONTAINER", "tapps-brain-db")
VISUAL_CONTAINER = os.environ.get("TAPPS_BRAIN_VISUAL_CONTAINER", "tapps-visual")
DB_USER = os.environ.get("TAPPS_BRAIN_DB_USER", "tapps")
DB_NAME = os.environ.get("TAPPS_BRAIN_DB_NAME", "tapps_brain")
BASE_URL = os.environ.get("TAPPS_BRAIN_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
VISUAL_URL = os.environ.get("TAPPS_BRAIN_VISUAL_URL", "http://127.0.0.1:8088").rstrip("/")
OUT_DIR = Path(os.environ.get("OUT_DIR", str(REPO_ROOT / "brain-eval-reports")))

HTTP_OK = 200

# Finding thresholds (tune here; surfaced in section 0 of the report).
COMPOSITE_OK = 0.7
CONTRA_PCT_ALERT = 40
TESTISH_KEYS_ALERT = 100
STALE_UNUSED_ALERT = 1000
FRESHNESS_ALERT = 0.5


def _load_token(name: str) -> str | None:
    """Resolve a token from the environment, falling back to docker/.env."""
    val = os.environ.get(name)
    if val:
        return val
    env_path = REPO_ROOT / "docker" / ".env"
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{name}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


AUTH_TOKEN = _load_token("TAPPS_BRAIN_AUTH_TOKEN")
METRICS_TOKEN = _load_token("TAPPS_BRAIN_METRICS_TOKEN")


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str, str]:
    """Run a command, returning (rc, stdout, stderr). Never raises on non-zero."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    return proc.returncode, proc.stdout, proc.stderr


def _http_get(url: str, token: str | None = None, timeout: int = 30) -> tuple[int, str]:
    """GET a URL, returning (status, body). Status 0 on transport error."""
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, str(exc)


def _psql_json(query: str, timeout: int = 60) -> Any:  # noqa: ANN401 - JSON value of any shape
    """Execute a query in the DB container, returning parsed JSON (or error dict).

    The query MUST be wrapped so it yields a single JSON value, e.g.
    ``SELECT json_agg(t) FROM (...) t;``.
    """
    rc, out, err = _run(
        [
            "docker",
            "exec",
            DB_CONTAINER,
            "psql",
            "-U",
            DB_USER,
            "-d",
            DB_NAME,
            "-t",
            "-A",
            "-c",
            query,
        ],
        timeout=timeout,
    )
    if rc != 0:
        return {"_error": err.strip() or f"psql rc={rc}"}
    body = out.strip()
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"_raw": body}


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------


def collect_stack() -> dict[str, Any]:
    """Container image, uptime, restart count, and health for each service."""
    out: dict[str, Any] = {}
    for name in (HTTP_CONTAINER, DB_CONTAINER, VISUAL_CONTAINER):
        fmt = (
            "{{.Config.Image}}|{{.State.Status}}|{{.State.StartedAt}}|"
            "{{.RestartCount}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}"
        )
        rc, res, err = _run(["docker", "inspect", name, "--format", fmt])
        if rc != 0:
            out[name] = {"_error": err.strip() or "not found"}
            continue
        image, status, started, restarts, health = (res.strip().split("|") + [""] * 5)[:5]
        uptime_s = None
        try:
            started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            uptime_s = (datetime.now(UTC) - started_dt).total_seconds()
        except ValueError:
            pass
        out[name] = {
            "image": image,
            "status": status,
            "started_at": started,
            "uptime_hours": round(uptime_s / 3600, 2) if uptime_s else None,
            "restart_count": restarts,
            "health": health,
        }
    return out


def collect_http() -> dict[str, Any]:
    """Probe the HTTP control plane: health, ready, metrics, snapshot."""
    out: dict[str, Any] = {}

    status, body = _http_get(f"{BASE_URL}/healthz?deep=1")
    out["healthz"] = {"status": status, "body": _maybe_json(body)}

    status, body = _http_get(f"{BASE_URL}/ready")
    out["ready"] = {"status": status, "body": _maybe_json(body)}

    status, body = _http_get(f"{BASE_URL}/metrics", token=METRICS_TOKEN)
    out["metrics"] = {
        "status": status,
        "parsed": _parse_metrics(body) if status == HTTP_OK else None,
    }

    # Prefer the visual cached snapshot (cheap), fall back to direct /snapshot.
    status, body = _http_get(f"{VISUAL_URL}/snapshot", timeout=35)
    if status != HTTP_OK:
        status, body = _http_get(f"{BASE_URL}/snapshot", token=AUTH_TOKEN, timeout=35)
    out["snapshot"] = {"status": status, "body": _maybe_json(body)}
    return out


def _maybe_json(body: str) -> Any:  # noqa: ANN401 - parsed JSON of any shape
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return {"_raw": body[:500]}


def _parse_metrics(text: str) -> dict[str, Any]:
    """Extract the interesting metric families from Prometheus exposition text."""
    parsed: dict[str, Any] = {
        "process_uptime_seconds": None,
        "db_ready": None,
        "tool_calls": [],
        "mcp_requests": [],
        "profile_tools_call": [],
        "snapshot_cache_hits_total": None,
        "profile_cache_events": [],
        "probe_cache_hit": {},
        "pool": {},
        "missing_indexes": [],
    }
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split("{", 1)[0].split(" ", 1)[0]
        try:
            value = float(line.rsplit(" ", 1)[1])
        except (IndexError, ValueError):
            continue
        labels = _labels(line)
        if name == "tapps_brain_process_uptime_seconds":
            parsed["process_uptime_seconds"] = value
        elif name == "tapps_brain_db_ready":
            parsed["db_ready"] = value
        elif name == "tapps_brain_tool_calls_total":
            parsed["tool_calls"].append({**labels, "count": value})
        elif name == "tapps_brain_mcp_requests_total":
            parsed["mcp_requests"].append({**labels, "count": value})
        elif name == "tapps_brain_mcp_tools_call_total":
            parsed["profile_tools_call"].append({**labels, "count": value})
        elif name == "tapps_brain_snapshot_cache_hits_total":
            parsed["snapshot_cache_hits_total"] = value
        elif name == "tapps_brain_mcp_profile_cache_events_total":
            parsed["profile_cache_events"].append({**labels, "count": value})
        elif name == "tapps_brain_mcp_probe_duration_seconds_count":
            parsed["probe_cache_hit"][labels.get("cache_hit", "?")] = value
        elif name.startswith("tapps_brain_pool_"):
            parsed["pool"][name.replace("tapps_brain_pool_", "")] = value
        elif name == "tapps_brain_private_missing_indexes_total":
            parsed["missing_indexes"].append({**labels, "count": value})
    return parsed


def _labels(line: str) -> dict[str, str]:
    match = re.search(r"\{(.*)\}", line)
    if not match:
        return {}
    out: dict[str, str] = {}
    for pair in re.findall(r'(\w+)="([^"]*)"', match.group(1)):
        out[pair[0]] = pair[1]
    return out


def collect_db(window_hours: int) -> dict[str, Any]:
    """Run the persistent-store analytics queries for the window."""
    w = f"now() - interval '{int(window_hours)} hours'"
    queries: dict[str, str] = {
        "table_activity": f"""
            SELECT json_agg(t) FROM (
              SELECT 'private_memories' AS tbl, count(*) total,
                     count(*) FILTER (WHERE created_at > {w}) created_in_window,
                     count(*) FILTER (WHERE last_accessed > {w}) accessed_in_window
              FROM private_memories
              UNION ALL SELECT 'experience_events', count(*),
                     count(*) FILTER (WHERE event_time > {w}), NULL FROM experience_events
              UNION ALL SELECT 'feedback_events', count(*),
                     count(*) FILTER (WHERE timestamp > {w}), NULL FROM feedback_events
              UNION ALL SELECT 'audit_log', count(*),
                     count(*) FILTER (WHERE timestamp > {w}), NULL FROM audit_log
              UNION ALL SELECT 'diagnostics_history', count(*),
                     count(*) FILTER (WHERE recorded_at > {w}), NULL FROM diagnostics_history
              UNION ALL SELECT 'kg_entities', count(*),
                     count(*) FILTER (WHERE created_at > {w}), NULL FROM kg_entities
              UNION ALL SELECT 'kg_edges', count(*),
                     count(*) FILTER (WHERE created_at > {w}), NULL FROM kg_edges
              UNION ALL SELECT 'hive_memories', count(*),
                     count(*) FILTER (WHERE created_at > {w}), NULL FROM hive_memories
              UNION ALL SELECT 'session_chunks', count(*),
                     count(*) FILTER (WHERE created_at > {w}), NULL FROM session_chunks
              UNION ALL SELECT 'gc_archive', count(*),
                     count(*) FILTER (WHERE archived_at > {w}), NULL FROM gc_archive
            ) t;
        """,
        "memory_tier_dist": """
            SELECT json_agg(t) FROM (
              SELECT tier, count(*) entries, round(avg(confidence)::numeric, 3) avg_conf,
                     count(*) FILTER (WHERE contradicted) contradicted,
                     count(*) FILTER (WHERE coalesce(access_count,0) = 0) never_accessed
              FROM private_memories GROUP BY tier ORDER BY entries DESC
            ) t;
        """,
        "memory_status_dist": """
            SELECT json_agg(t) FROM (
              SELECT coalesce(status,'(null)') status, coalesce(stale_reason,'-') stale_reason,
                     count(*) n
              FROM private_memories GROUP BY 1,2 ORDER BY n DESC LIMIT 15
            ) t;
        """,
        "memory_access_buckets": """
            SELECT json_agg(t) FROM (
              SELECT CASE
                       WHEN coalesce(access_count,0) = 0 THEN '0 (never recalled)'
                       WHEN access_count BETWEEN 1 AND 2 THEN '1-2'
                       WHEN access_count BETWEEN 3 AND 5 THEN '3-5'
                       ELSE '6+'
                     END bucket,
                     count(*) entries
              FROM private_memories GROUP BY 1 ORDER BY 1
            ) t;
        """,
        "memory_by_project": f"""
            SELECT json_agg(t) FROM (
              SELECT project_id, agent_id, count(*) entries,
                     count(*) FILTER (WHERE contradicted) contradicted,
                     round(100.0 * count(*) FILTER (WHERE contradicted)
                           / nullif(count(*), 0), 1) contra_pct,
                     count(*) FILTER (WHERE created_at > {w}) created_in_window,
                     count(*) FILTER (WHERE last_accessed > {w}) accessed_in_window,
                     max(created_at) last_write
              FROM private_memories GROUP BY 1,2 ORDER BY entries DESC LIMIT 20
            ) t;
        """,
        "data_hygiene": f"""
            SELECT json_agg(t) FROM (
              SELECT count(*) total,
                     count(*) FILTER (WHERE contradicted) contradicted,
                     round(100.0 * count(*) FILTER (WHERE contradicted)
                           / nullif(count(*), 0), 1) contra_pct,
                     count(*) FILTER (
                       WHERE key ~ '(thread-|corruption-|compat-|-test-|^test-|smoke|bench|load)'
                     ) testish_keys,
                     count(*) FILTER (
                       WHERE created_at < now() - interval '30 days'
                         AND coalesce(access_count, 0) <= 1
                     ) stale_unused,
                     count(*) FILTER (WHERE last_accessed > {w}) accessed_in_window
              FROM private_memories
            ) t;
        """,
        "feedback_window": f"""
            SELECT json_agg(t) FROM (
              SELECT event_type, count(*) n, round(avg(utility_score)::numeric,3) avg_utility
              FROM feedback_events WHERE timestamp > {w}
              GROUP BY 1 ORDER BY n DESC
            ) t;
        """,
        "experience_window": f"""
            SELECT json_agg(t) FROM (
              SELECT event_type, count(*) n, count(DISTINCT agent_id) agents,
                     round(avg(utility_score)::numeric,3) avg_utility
              FROM experience_events WHERE event_time > {w}
              GROUP BY 1 ORDER BY n DESC
            ) t;
        """,
        "audit_window": f"""
            SELECT json_agg(t) FROM (
              SELECT event_type, count(*) n
              FROM audit_log WHERE timestamp > {w}
              GROUP BY 1 ORDER BY n DESC LIMIT 25
            ) t;
        """,
        "diagnostics_window": f"""
            SELECT json_agg(t) FROM (
              SELECT project_id, count(*) samples,
                     round(min(composite_score)::numeric,3) min_score,
                     round(avg(composite_score)::numeric,3) avg_score,
                     round(max(composite_score)::numeric,3) max_score,
                     (array_agg(circuit_state ORDER BY recorded_at DESC))[1] latest_circuit,
                     (array_agg(composite_score ORDER BY recorded_at DESC))[1] latest_score,
                     max(recorded_at) latest_at
              FROM diagnostics_history WHERE recorded_at > {w}
              GROUP BY 1 ORDER BY samples DESC
            ) t;
        """,
        "kg_summary": f"""
            SELECT json_agg(t) FROM (
              SELECT 'entities' kind, count(*) total,
                     count(*) FILTER (WHERE created_at > {w}) created_in_window,
                     count(*) FILTER (WHERE contradicted) contradicted,
                     count(*) FILTER (WHERE status <> 'active') non_active
              FROM kg_entities
              UNION ALL SELECT 'edges', count(*),
                     count(*) FILTER (WHERE created_at > {w}),
                     count(*) FILTER (WHERE contradicted),
                     count(*) FILTER (WHERE status <> 'active')
              FROM kg_edges
            ) t;
        """,
        "gc_window": f"""
            SELECT json_agg(t) FROM (
              SELECT count(*) archived, coalesce(sum(byte_count),0) bytes,
                     count(DISTINCT project_id) projects
              FROM gc_archive WHERE archived_at > {w}
            ) t;
        """,
        "hive_summary": f"""
            SELECT json_agg(t) FROM (
              SELECT namespace, count(*) entries,
                     count(*) FILTER (WHERE created_at > {w}) created_in_window,
                     count(*) FILTER (WHERE superseded_by IS NOT NULL) superseded
              FROM hive_memories GROUP BY 1 ORDER BY entries DESC LIMIT 15
            ) t;
        """,
        "embedding_coverage": """
            SELECT json_agg(t) FROM (
              SELECT count(*) total,
                     count(embedding) with_embedding,
                     count(*) - count(embedding) missing_embedding
              FROM private_memories
            ) t;
        """,
    }
    results: dict[str, Any] = {}
    for key, sql in queries.items():
        results[key] = _psql_json(sql)
    return results


def collect_logs(window_hours: int) -> dict[str, Any]:
    """Extract error/warning/traceback lines from the container logs."""
    _rc, out, err = _run(
        ["docker", "logs", "--since", f"{int(window_hours)}h", "--timestamps", HTTP_CONTAINER],
        timeout=60,
    )
    # docker logs writes app output to stderr for many images; merge both.
    combined = (out or "") + (err or "")
    lines = [ln for ln in combined.splitlines() if ln.strip()]
    span_first = lines[0][:30] if lines else None
    span_last = lines[-1][:30] if lines else None

    pattern = re.compile(r"error|warning|traceback|exception|critical|failed|fatal", re.IGNORECASE)
    matches = [ln for ln in lines if pattern.search(ln)]

    # Bucket matches by a normalized signature (drop timestamps/ids/numbers).
    buckets: dict[str, dict[str, Any]] = {}
    for ln in matches:
        sig = re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z?", "", ln)
        sig = re.sub(r"[0-9a-f]{8,}", "<hex>", sig)
        sig = re.sub(r"\d+", "<n>", sig)
        sig = sig.strip()[:200]
        b = buckets.setdefault(sig, {"count": 0, "sample": ln[:400]})
        b["count"] += 1
    top = sorted(buckets.values(), key=lambda x: x["count"], reverse=True)[:20]
    return {
        "total_log_lines": len(lines),
        "log_span_first": span_first,
        "log_span_last": span_last,
        "match_count": len(matches),
        "distinct_signatures": len(buckets),
        "top_signatures": top,
    }


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    if not rows:
        return "_(none)_\n"
    head = "| " + " | ".join(cols) + " |\n"
    sep = "| " + " | ".join("---" for _ in cols) + " |\n"
    body = ""
    for r in rows:
        body += "| " + " | ".join(str(r.get(c, "")) for c in cols) + " |\n"
    return head + sep + body


def derive_findings(data: dict[str, Any]) -> list[str]:
    """Threshold-based, self-explaining findings so re-runs flag regressions."""
    out: list[str] = []
    db = data["db"]
    http = data["http"]
    m = (http.get("metrics") or {}).get("parsed") or {}
    snap = (http.get("snapshot") or {}).get("body") or {}
    diag = snap.get("diagnostics") or {}

    hz = (http.get("healthz") or {}).get("body") or {}
    comp = diag.get("composite_score")
    if hz.get("ok") and (comp is None or comp >= COMPOSITE_OK):
        out.append(f"OK: stack healthy (circuit={hz.get('circuit_state')}, composite={comp}).")
    elif not hz.get("ok"):
        out.append("CRITICAL: /healthz reports not-ok - investigate db/mcp/experience path.")

    if isinstance(db.get("data_hygiene"), list) and db["data_hygiene"]:
        h = db["data_hygiene"][0]
        if (h.get("contra_pct") or 0) >= CONTRA_PCT_ALERT:
            out.append(
                f"HIGH contradiction: {h.get('contra_pct')}% of {h.get('total')} entries are "
                "contradicted - save-time conflict invalidation is firing on similar context-tier "
                "memories. Review the conflict-policy similarity threshold for ingest-heavy loads."
            )
        if (h.get("testish_keys") or 0) > TESTISH_KEYS_ALERT:
            out.append(
                f"TEST POLLUTION: {h.get('testish_keys')} entries have test/load/smoke-style keys "
                "in persistent storage. Smoke/load/contract tests should use throwaway project_ids "
                "or an ephemeral tier, not real project rows."
            )
        if (h.get("stale_unused") or 0) > STALE_UNUSED_ALERT:
            out.append(
                f"GC BACKLOG: {h.get('stale_unused')} entries are >30d old and recalled <=1x. "
                "Schedule periodic `maintenance gc` (AUTO_GC) so the store reflects live knowledge."
            )

    dims = diag.get("dimensions") or {}
    fr = dims.get("freshness") or {}
    if isinstance(fr.get("score"), int | float) and fr["score"] < FRESHNESS_ALERT:
        out.append(
            f"FRESHNESS low ({round(fr['score'], 2)}): store is dominated by short-lived context "
            "tier near the 14d half-life. Expected for ingest workloads; consider promoting "
            "durable facts to pattern/architectural tiers or trimming context churn."
        )

    if isinstance(db.get("embedding_coverage"), list) and db["embedding_coverage"]:
        ec = db["embedding_coverage"][0]
        if (ec.get("missing_embedding") or 0) > 0:
            out.append(
                f"EMBEDDINGS: {ec.get('missing_embedding')} entries lack an embedding "
                "(vector recall falls back to lexical). Run scripts/backfill_embeddings.py."
            )

    blank_label_calls = sum(
        int(e["count"])
        for e in m.get("tool_calls", [])
        if not e.get("project_id") and not e.get("agent_id")
    )
    if blank_label_calls:
        out.append(
            f"OBSERVABILITY: {blank_label_calls} tool calls recorded with empty "
            "project_id/agent_id labels - per-tenant attribution is lost in /metrics. "
            "tapps-mcp/brain should propagate X-Project-Id / X-Agent-Id into the tool-call counter."
        )

    denied = [
        e for e in m.get("profile_tools_call", []) if e.get("outcome", "").startswith("denied")
    ]
    for e in denied:
        out.append(
            f"PROFILE FRICTION: profile '{e.get('profile')}' was denied tool "
            f"'{e.get('tool')}' {int(e['count'])}x - widen the profile or stop the client from "
            "calling a tool it cannot use."
        )

    if isinstance(db.get("experience_window"), list):
        dep = next(
            (r for r in db["experience_window"] if r.get("event_type") == "deprecated_tool_call"),
            None,
        )
        if dep and (dep.get("n") or 0) > 0:
            out.append(
                f"DEPRECATED TOOLS: {dep['n']} deprecated_tool_call events in window - a client is "
                "still calling retired tools. (The event payload omits the tool name - an "
                "observability gap worth fixing in tapps-brain.)"
            )

    if isinstance(db.get("feedback_window"), list):
        gaps = sum(
            r.get("n", 0) for r in db["feedback_window"] if "gap" in (r.get("event_type") or "")
        )
        if gaps:
            out.append(
                f"FLYWHEEL: {gaps} recall-gap events in window - real queries returned nothing "
                "useful. Mine feedback_events.details for high-value missing knowledge to seed."
            )

    if not out:
        out.append("No threshold findings - brain is operating within expected bounds.")
    return out


def render_markdown(data: dict[str, Any]) -> str:  # noqa: PLR0915 - report builder
    w = data["window_hours"]
    db = data["db"]
    http = data["http"]
    m = (http.get("metrics") or {}).get("parsed") or {}
    lines: list[str] = []
    a = lines.append

    a(f"# tapps-brain evaluation - last {w}h")
    a("")
    a(f"_Generated {data['generated_at']} - run `python3 scripts/brain_eval.py` to refresh._")
    a("")
    a(
        "> Coverage note: Postgres analytics cover the full window. `/metrics` "
        "counters are in-memory and only cover time since the last process start. "
        "Container logs cover only the current container's retained output."
    )
    a("")

    # --- Automated findings ---
    a("## 0. Automated findings")
    a("")
    for f in derive_findings(data):
        a(f"- {f}")
    a("")

    # --- Stack ---
    a("## 1. Stack status")
    a("")
    rows = []
    for name, info in data["stack"].items():
        if "_error" in info:
            rows.append({"service": name, "image": "ERROR", "status": info["_error"]})
        else:
            rows.append(
                {
                    "service": name,
                    "image": info["image"],
                    "status": info["status"],
                    "health": info["health"],
                    "uptime_h": info["uptime_hours"],
                    "restarts": info["restart_count"],
                }
            )
    a(_table(rows, ["service", "image", "status", "health", "uptime_h", "restarts"]))

    # --- Health ---
    a("## 2. Live health")
    a("")
    hz = (http.get("healthz") or {}).get("body") or {}
    rd = (http.get("ready") or {}).get("body") or {}
    a(
        f"- `/healthz?deep=1`: ok={hz.get('ok')} db_ok={hz.get('db_ok')} "
        f"mcp_ok={hz.get('mcp_ok')} circuit={hz.get('circuit_state')} "
        f"queue_depth={hz.get('queue_depth')} version={hz.get('brain_version')}"
    )
    a(f"- experience_writable={hz.get('experience_writable')} ({hz.get('experience_detail')})")
    a(f"- `/ready`: status={rd.get('status')} migration_version={rd.get('migration_version')}")
    if m.get("process_uptime_seconds") is not None:
        a(
            f"- process uptime (metrics): {round(m['process_uptime_seconds'] / 3600, 2)}h "
            "-- in-memory counters below only cover this span"
        )
    a("")

    # --- Diagnostics scorecard ---
    a("## 3. Diagnostics scorecard")
    a("")
    snap = (http.get("snapshot") or {}).get("body") or {}
    diag = snap.get("diagnostics") or {}
    health_blk = snap.get("health") or {}
    if diag:
        a(
            f"- composite_score={diag.get('composite_score')} "
            f"circuit_state={diag.get('circuit_state')} "
            f"hive_composite={diag.get('hive_composite_score')}"
        )
    if health_blk:
        a(
            f"- entry_count={health_blk.get('entry_count')} "
            f"gc_candidates={health_blk.get('gc_candidates')}"
        )
    dims = diag.get("dimensions") or {}
    if dims:
        drows = [{"dimension": k, "score": (v or {}).get("score")} for k, v in sorted(dims.items())]
        a("")
        a(_table(drows, ["dimension", "score"]))
    if isinstance(db.get("diagnostics_window"), list):
        a("Diagnostics samples in window:")
        a("")
        a(
            _table(
                db["diagnostics_window"],
                [
                    "project_id",
                    "samples",
                    "min_score",
                    "avg_score",
                    "max_score",
                    "latest_score",
                    "latest_circuit",
                ],
            )
        )

    # --- Volume / growth ---
    a("## 4. Data volume & growth")
    a("")
    if isinstance(db.get("table_activity"), list):
        a(_table(db["table_activity"], ["tbl", "total", "created_in_window", "accessed_in_window"]))

    # --- Memory store ---
    a("## 5. Private memory store")
    a("")
    if isinstance(db.get("memory_tier_dist"), list):
        a("Tier distribution:")
        a("")
        a(
            _table(
                db["memory_tier_dist"],
                ["tier", "entries", "avg_conf", "contradicted", "never_accessed"],
            )
        )
    if isinstance(db.get("memory_access_buckets"), list):
        a("Recall reuse (access_count buckets):")
        a("")
        a(_table(db["memory_access_buckets"], ["bucket", "entries"]))
    if isinstance(db.get("embedding_coverage"), list) and db["embedding_coverage"]:
        ec = db["embedding_coverage"][0]
        a(
            f"- Embedding coverage: {ec.get('with_embedding')}/{ec.get('total')} "
            f"({ec.get('missing_embedding')} missing -> vector recall degraded for those)"
        )
        a("")
    if isinstance(db.get("memory_status_dist"), list):
        a("Status / stale-reason distribution:")
        a("")
        a(_table(db["memory_status_dist"], ["status", "stale_reason", "n"]))
    if isinstance(db.get("memory_by_project"), list):
        a("Top projects/agents by entries:")
        a("")
        a(
            _table(
                db["memory_by_project"],
                [
                    "project_id",
                    "agent_id",
                    "entries",
                    "contradicted",
                    "contra_pct",
                    "created_in_window",
                    "accessed_in_window",
                    "last_write",
                ],
            )
        )

    # --- Usage in window ---
    a("## 6. Usage in window")
    a("")
    if isinstance(db.get("audit_window"), list):
        a("Audit-log operations (write-path activity):")
        a("")
        a(_table(db["audit_window"], ["event_type", "n"]))
    if isinstance(db.get("experience_window"), list):
        a("Experience events:")
        a("")
        a(_table(db["experience_window"], ["event_type", "n", "agents", "avg_utility"]))
    if isinstance(db.get("feedback_window"), list):
        a("Feedback events (flywheel signals):")
        a("")
        a(_table(db["feedback_window"], ["event_type", "n", "avg_utility"]))

    # --- KG + Hive ---
    a("## 7. Knowledge graph & Hive")
    a("")
    if isinstance(db.get("kg_summary"), list):
        a(
            _table(
                db["kg_summary"],
                ["kind", "total", "created_in_window", "contradicted", "non_active"],
            )
        )
    if isinstance(db.get("hive_summary"), list):
        a("Hive namespaces:")
        a("")
        a(_table(db["hive_summary"], ["namespace", "entries", "created_in_window", "superseded"]))
    if isinstance(db.get("gc_window"), list) and db["gc_window"]:
        g = db["gc_window"][0]
        a(
            f"- GC archived in window: {g.get('archived')} entries / "
            f"{g.get('bytes')} bytes across {g.get('projects')} projects"
        )
        a("")

    # --- Cache / MCP metrics ---
    a("## 8. Cache hits & MCP traffic (in-memory, since process start)")
    a("")
    a(f"- snapshot_cache_hits_total: {m.get('snapshot_cache_hits_total')}")
    if m.get("probe_cache_hit"):
        a(f"- tools/list probe counts by cache_hit: {m['probe_cache_hit']}")
    if m.get("profile_cache_events"):
        a(
            "- profile resolver cache events: "
            + ", ".join(f"{e.get('result')}={int(e['count'])}" for e in m["profile_cache_events"])
        )
    if m.get("pool"):
        a(f"- hive pool: {m['pool']}")
    if m.get("tool_calls"):
        a("")
        a("Tool calls (from /metrics):")
        a("")
        a(
            _table(
                [
                    {
                        "tool": e.get("tool"),
                        "status": e.get("status"),
                        "project_id": e.get("project_id"),
                        "agent_id": e.get("agent_id"),
                        "count": int(e["count"]),
                    }
                    for e in m["tool_calls"]
                ],
                ["tool", "status", "project_id", "agent_id", "count"],
            )
        )
    if m.get("profile_tools_call"):
        a("Profile-gated tool calls:")
        a("")
        a(
            _table(
                [
                    {
                        "profile": e.get("profile"),
                        "tool": e.get("tool"),
                        "outcome": e.get("outcome"),
                        "count": int(e["count"]),
                    }
                    for e in m["profile_tools_call"]
                ],
                ["profile", "tool", "outcome", "count"],
            )
        )
    if m.get("missing_indexes"):
        a(f"- WARNING missing HNSW indexes: {m['missing_indexes']}")

    # --- Logs ---
    a("## 9. Logs (errors / warnings)")
    a("")
    lg = data["logs"]
    a(
        f"- log lines available: {lg['total_log_lines']} "
        f"(span {lg['log_span_first']} .. {lg['log_span_last']})"
    )
    a(
        f"- error/warn/exception matches: {lg['match_count']} "
        f"across {lg['distinct_signatures']} distinct signatures"
    )
    a("")
    if lg["top_signatures"]:
        a("Top signatures:")
        a("")
        for s in lg["top_signatures"]:
            a(f"- ({s['count']}x) `{s['sample']}`")
    a("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"==> tapps-brain evaluation (window={WINDOW_HOURS}h)")
    if not METRICS_TOKEN:
        print("    note: TAPPS_BRAIN_METRICS_TOKEN not found - /metrics will be redacted")

    data: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "window_hours": WINDOW_HOURS,
        "base_url": BASE_URL,
    }
    print("    collecting stack status...")
    data["stack"] = collect_stack()
    print("    probing HTTP control plane...")
    data["http"] = collect_http()
    print("    running Postgres analytics...")
    data["db"] = collect_db(WINDOW_HOURS)
    print("    scanning container logs...")
    data["logs"] = collect_logs(WINDOW_HOURS)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = OUT_DIR / f"brain-eval-{stamp}.json"
    md_path = OUT_DIR / f"brain-eval-{stamp}.md"
    json_path.write_text(json.dumps(data, indent=2, default=str))
    report = render_markdown(data)
    md_path.write_text(report)

    print("")
    print(report)
    print("")
    print(f"==> wrote {md_path}")
    print(f"==> wrote {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
