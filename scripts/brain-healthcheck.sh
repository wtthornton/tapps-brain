#!/usr/bin/env bash
# scripts/brain-healthcheck.sh — verify this repo is wired to the deployed
# tapps-brain (container tapps-brain-http) and that the MCP round-trip works.
#
# Usage:
#   bash scripts/brain-healthcheck.sh           # from repo root
#   bash scripts/brain-healthcheck.sh --quiet   # suppress per-check output
#
# Exit codes: 0 all checks passed, 1 warnings only, 2 at least one failure.
#
# The script reads .mcp.json for the MCP URL + X-Project-Id + X-Agent-Id and
# .env (or the current shell) for TAPPS_BRAIN_AUTH_TOKEN. No secrets are ever
# printed. Companion doc: docs/guides/mcp-client-repo-setup.md.

set -uo pipefail

QUIET=0
if [[ "${1:-}" == "--quiet" ]]; then
    QUIET=1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Load .env if token isn't already exported (covers shells without direnv).
if [[ -f .env && -z "${TAPPS_BRAIN_AUTH_TOKEN:-}" ]]; then
    set -o allexport
    # shellcheck disable=SC1091
    source .env
    set +o allexport
fi

PASS=0
FAIL=0
WARN=0

if [[ -t 1 ]]; then
    C_OK='\033[32m'; C_WARN='\033[33m'; C_FAIL='\033[31m'; C_B='\033[1m'; C_0='\033[0m'
else
    C_OK=''; C_WARN=''; C_FAIL=''; C_B=''; C_0=''
fi

say() { [[ $QUIET -eq 0 ]] && printf '%b\n' "$*"; }
pass() { say "  ${C_OK}[OK]${C_0}    $*"; PASS=$((PASS + 1)); }
warn() { say "  ${C_WARN}[WARN]${C_0}  $*"; WARN=$((WARN + 1)); }
fail() { say "  ${C_FAIL}[FAIL]${C_0}  $*"; FAIL=$((FAIL + 1)); }
section() { say ""; say "${C_B}== $* ==${C_0}"; }

# Extract a JSON path from .mcp.json; echoes empty string on miss.
jq_path() {
    python3 -c "
import json, sys
try:
    d = json.load(open('.mcp.json'))
    node = d
    for p in sys.argv[1:]:
        node = node[p]
    print(node)
except Exception:
    pass
" "$@"
}

# Parse a Streamable-HTTP MCP response (JSON or SSE). Emits the first JSON body.
# Uses python3 -c so the heredoc does not consume stdin that the pipe supplies.
extract_json_body() {
    python3 -c '
import sys
data = sys.stdin.read()
lines = [l[5:].lstrip() for l in data.splitlines() if l.startswith("data:")]
sys.stdout.write("\n".join(lines) if lines else data)
'
}

# Stateful Streamable-HTTP flow: POST /mcp/ initialize, capture mcp-session-id,
# send notifications/initialized, then drive further requests with that sid.
mcp_initialize() {
    curl -sS -D - -o /dev/null -X POST "$MCP_URL" \
        -H "Authorization: Bearer ${TAPPS_BRAIN_AUTH_TOKEN}" \
        -H "X-Project-Id: ${PROJECT_ID}" \
        -H "X-Agent-Id: ${AGENT_ID}" \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' \
        --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"brain-healthcheck","version":"1.0"}}}' \
        2>/dev/null \
        | awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}' | tr -d '\r\n'
}

mcp_notify_initialized() {
    local sid="$1"
    curl -sS -o /dev/null -X POST "$MCP_URL" \
        -H "Authorization: Bearer ${TAPPS_BRAIN_AUTH_TOKEN}" \
        -H "X-Project-Id: ${PROJECT_ID}" \
        -H "X-Agent-Id: ${AGENT_ID}" \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' \
        -H "Mcp-Session-Id: ${sid}" \
        --data '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}' 2>/dev/null || true
}

# mcp_call <session-id> <json-rpc body> — emits parsed JSON body.
mcp_call() {
    local sid="$1" body="$2"
    curl -sS -X POST "$MCP_URL" \
        -H "Authorization: Bearer ${TAPPS_BRAIN_AUTH_TOKEN}" \
        -H "X-Project-Id: ${PROJECT_ID}" \
        -H "X-Agent-Id: ${AGENT_ID}" \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' \
        -H "Mcp-Session-Id: ${sid}" \
        --data "$body" 2>/dev/null | extract_json_body
}

# ---------------------------------------------------------------------------
# 1. Repo-local wiring
# ---------------------------------------------------------------------------
section "Repo-local wiring"

if [[ -f .mcp.json ]]; then
    pass ".mcp.json present"
else
    fail ".mcp.json missing — see docs/guides/mcp-client-repo-setup.md"
fi

MCP_URL="$(jq_path mcpServers tapps-brain url)"
PROJECT_ID="$(jq_path mcpServers tapps-brain headers X-Project-Id)"
AGENT_ID="$(jq_path mcpServers tapps-brain headers X-Agent-Id)"

[[ -n "$MCP_URL"    ]] && pass "MCP URL: $MCP_URL"      || fail "tapps-brain.url missing from .mcp.json"
[[ -n "$PROJECT_ID" ]] && pass "X-Project-Id: $PROJECT_ID" || fail "X-Project-Id missing from .mcp.json"
[[ -n "$AGENT_ID"   ]] && pass "X-Agent-Id: $AGENT_ID"     || fail "X-Agent-Id missing from .mcp.json"

if [[ -f .env ]]; then
    mode="$(stat -c '%a' .env 2>/dev/null || stat -f '%Lp' .env 2>/dev/null || echo '???')"
    if [[ "$mode" == "600" ]]; then
        pass ".env present (chmod 600)"
    else
        warn ".env present but chmod is $mode (recommended: 600)"
    fi
else
    fail ".env missing — bearer token cannot be loaded"
fi

if grep -qxE '\.env' .gitignore 2>/dev/null; then
    pass ".env is gitignored"
else
    fail ".env NOT listed in .gitignore"
fi

if [[ -f .envrc ]]; then
    pass ".envrc present"
else
    warn ".envrc missing — direnv auto-load won't work"
fi

if command -v direnv >/dev/null 2>&1; then
    pass "direnv installed"
else
    warn "direnv not installed (MCP clients must be launched from a shell with .env sourced)"
fi

if [[ -n "${TAPPS_BRAIN_AUTH_TOKEN:-}" ]]; then
    pass "TAPPS_BRAIN_AUTH_TOKEN loaded in env"
else
    fail "TAPPS_BRAIN_AUTH_TOKEN is not set in this shell"
fi

BRAIN_PROFILE="$(jq_path mcpServers tapps-brain headers X-Brain-Profile)"
if [[ -n "$BRAIN_PROFILE" ]]; then
    pass "X-Brain-Profile: $BRAIN_PROFILE (tool filter active)"
else
    warn "X-Brain-Profile not set — using 'full' profile (all 55 tools). Add header to .mcp.json to reduce context bloat."
fi

# ---------------------------------------------------------------------------
# 2. Server reachability
# ---------------------------------------------------------------------------
section "Server reachability"

if command -v docker >/dev/null 2>&1; then
    status="$(docker ps --filter name=tapps-brain-http --format '{{.Status}}')"
    if [[ -n "$status" ]]; then
        if [[ "$status" == *"healthy"* || "$status" == "Up"* ]]; then
            pass "tapps-brain-http container: $status"
        else
            warn "tapps-brain-http container: $status"
        fi
    else
        fail "tapps-brain-http container not running"
    fi
else
    warn "docker CLI not available — skipping container check"
fi

if [[ -n "$MCP_URL" ]]; then
    base_host="$(printf '%s' "$MCP_URL" | sed -E 's#(https?://[^/]+).*#\1#')"
    for ep in /health /ready /metrics /openapi.json; do
        code="$(curl -s -o /dev/null -w '%{http_code}' "${base_host}${ep}" 2>/dev/null || echo 000)"
        if [[ "$code" == "200" ]]; then
            pass "${base_host}${ep} → 200"
        else
            fail "${base_host}${ep} → $code"
        fi
    done

    noauth="$(curl -s -o /dev/null -w '%{http_code}' \
        -X POST "$MCP_URL" \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' \
        --data '{"jsonrpc":"2.0","id":0,"method":"tools/list","params":{}}' 2>/dev/null || echo 000)"
    case "$noauth" in
        401|403) pass "$MCP_URL rejects unauthenticated ($noauth)" ;;
        200)     warn "$MCP_URL responded 200 without auth — auth may be disabled" ;;
        *)       fail "$MCP_URL unexpected unauthenticated status $noauth" ;;
    esac
fi

# ---------------------------------------------------------------------------
# 3. Authenticated MCP round-trip
# ---------------------------------------------------------------------------
section "Authenticated MCP round-trip"

if [[ -z "${TAPPS_BRAIN_AUTH_TOKEN:-}" || -z "$MCP_URL" || -z "$PROJECT_ID" || -z "$AGENT_ID" ]]; then
    fail "skipping — token, URL, or headers missing"
else
    SID="$(mcp_initialize)"
    if [[ -z "$SID" ]]; then
        fail "MCP initialize did not return mcp-session-id (stateful Streamable HTTP expected)"
    else
        pass "MCP initialize → session ${SID:0:8}…"
        mcp_notify_initialized "$SID"

        tl_body="$(mcp_call "$SID" '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}')"
        tool_count="$(printf '%s' "$tl_body" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    print(len(d.get("result", {}).get("tools", [])))
except Exception:
    print(0)
' 2>/dev/null)"
        if [[ "${tool_count:-0}" -gt 0 ]]; then
            pass "tools/list → ${tool_count} tools"
        else
            fail "tools/list returned no tools (body snippet: $(printf '%s' "$tl_body" | head -c 200))"
        fi

        tool_names="$(printf '%s' "$tl_body" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    print(" ".join(t.get("name", "") for t in d.get("result", {}).get("tools", [])))
except Exception:
    pass
' 2>/dev/null)"
        for t in brain_recall brain_remember memory_save memory_search memory_recall memory_delete; do
            if printf ' %s ' "$tool_names" | grep -q " $t "; then
                pass "tool exposed: $t"
            else
                warn "expected tool missing: $t"
            fi
        done

        rc_body="$(mcp_call "$SID" '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"brain_recall","arguments":{"query":"brain healthcheck ping","limit":1}}}')"
        rc_verdict="$(printf '%s' "$rc_body" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    if "error" in d:
        print("ERR:" + str(d["error"].get("message", "unknown")))
    else:
        r = d.get("result", {})
        is_err = r.get("isError", False)
        if is_err:
            content = r.get("content", [])
            msg = content[0].get("text", "error") if content else "error"
            print("ERR:" + msg[:180])
        else:
            print("OK")
except Exception as e:
    print("ERR:parse:" + str(e))
' 2>/dev/null)"
        case "$rc_verdict" in
            OK)    pass "brain_recall round-trip succeeded (tenant isolation + token verified)" ;;
            ERR:*) fail "brain_recall call errored: ${rc_verdict#ERR:}" ;;
            *)     fail "brain_recall round-trip: no parseable response (snippet: $(printf '%s' "$rc_body" | head -c 200))" ;;
        esac

        # Best-effort session termination; stateful servers may 400 on DELETE.
        curl -sS -o /dev/null -X DELETE "$MCP_URL" \
            -H "Authorization: Bearer ${TAPPS_BRAIN_AUTH_TOKEN}" \
            -H "Mcp-Session-Id: ${SID}" 2>/dev/null || true
    fi
fi

# ---------------------------------------------------------------------------
# 4. Project registration (server-side)
# ---------------------------------------------------------------------------
section "Project registration (server-side)"

if command -v docker >/dev/null 2>&1 && [[ -n "$PROJECT_ID" ]] \
    && docker ps --filter name=tapps-brain-http --format '{{.Names}}' | grep -q '^tapps-brain-http$'; then
    proj_line="$(docker exec tapps-brain-http tapps-brain project list 2>/dev/null \
        | grep -E "^\[[A-Z[:space:]]+\][[:space:]]+${PROJECT_ID}[[:space:]]" | head -n1)"
    if [[ -n "$proj_line" ]]; then
        pass "project registered: $(printf '%s' "$proj_line" | tr -s ' ')"
    else
        fail "project '${PROJECT_ID}' not registered on brain — run: docker exec tapps-brain-http tapps-brain project register ${PROJECT_ID}"
    fi
else
    warn "container unavailable — skipping project-list check"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
section "Summary"
say "passed:${PASS}  warnings:${WARN}  failed:${FAIL}"

if (( FAIL > 0 )); then
    exit 2
elif (( WARN > 0 )); then
    exit 1
fi
exit 0
