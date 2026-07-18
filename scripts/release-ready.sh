#!/usr/bin/env bash
# Production release gate: packaging, Python QA.
#
# Usage (Linux / macOS / WSL):
#   bash scripts/release-ready.sh
#
# From repo root with uv on PATH:
#   uv sync --group dev && bash scripts/release-ready.sh
#
# CI fast path (skip full pytest when matrix already ran tests):
#   SKIP_FULL_PYTEST=1 bash scripts/release-ready.sh
#
# Windows (native): use WSL or Git Bash; see docs/planning/STATUS.md (WSL / Windows).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SKIP_FULL_PYTEST="${SKIP_FULL_PYTEST:-0}"
SKIP_BENCHMARKS="${SKIP_BENCHMARKS:-1}"
SKIP_LINT="${SKIP_LINT:-0}"
# TAP-570: set TAPPS_BRAIN_CROSS_TENANT_SMOKE=1 to include the cross-tenant HTTP
# denial smoke test.  Requires a live sidecar on TAPPS_BRAIN_SIDECAR_URL (default
# http://localhost:8080) with TAPPS_BRAIN_ADMIN_TOKEN + TAPPS_BRAIN_AUTH_TOKEN set.
# Strongly recommended before any production deployment that touches RLS, the HTTP
# adapter, or per-tenant auth.  Off by default to avoid blocking release gates in
# environments without a running sidecar.
TAPPS_BRAIN_CROSS_TENANT_SMOKE="${TAPPS_BRAIN_CROSS_TENANT_SMOKE:-0}"
# Docs gate (smoke): verifies the critical user-facing docs survived the release
# build.  Off by default — set TAPPS_BRAIN_DOCS_GATE=1 to require it.  This is a
# *smoke* check, not a full drift audit; the full audit runs through the docsmcp
# MCP tools (docs_check_drift / docs_release_gate) from an agent or in CI.
TAPPS_BRAIN_DOCS_GATE="${TAPPS_BRAIN_DOCS_GATE:-0}"

fail() {
  echo "release-ready: FAILED — $*" >&2
  echo "Remediation: scripts/publish-checklist.md" >&2
  exit 1
}

need_uv() {
  command -v uv >/dev/null 2>&1 || fail "uv not found (install: https://github.com/astral-sh/uv)"
}

echo "==> [1/6] uv sync --group dev"
need_uv
uv sync --group dev

echo "==> [2/6] Packaging build (clean dist/)"
rm -rf dist/
uv build

echo "==> [3/6] Wheel smoke install + import"
uv venv .venv-release-smoke --clear
# shellcheck disable=SC2035
uv pip install --python .venv-release-smoke dist/*.whl
if [[ -x .venv-release-smoke/bin/python ]]; then
  .venv-release-smoke/bin/python -c "from tapps_brain import __version__; print(f'import ok: tapps_brain {__version__}')"
else
  fail "expected .venv-release-smoke/bin/python (use WSL/Git Bash on Windows)"
fi
# Clean up the throwaway smoke venv — it is ~5 GB (torch + CUDA libs) and was
# previously left on disk after every run, accumulating across releases.
rm -rf .venv-release-smoke

echo "==> [4/6] Version consistency tests"
uv run pytest tests/unit/test_version_consistency.py -v --tb=short || fail "version consistency (includes docker/.env.example BRAIN_VERSION)"

if [[ "$SKIP_FULL_PYTEST" == "1" ]]; then
  echo "==> [5/6] Full pytest suite (skipped: SKIP_FULL_PYTEST=1)"
else
  echo "==> [5/6] Full pytest suite (no benchmarks, coverage gate)"
  # Coverage threshold: 80 (v3.15.0).  EPIC-076 added ~2k LOC of Postgres-backed
  # code (postgres_kg, kg_service, experience) whose full coverage requires a
  # live pgvector sidecar that this gate does not start.  See TAP follow-up:
  # raise back to 88 once the gate runs a Postgres sidecar or .coveragerc
  # excludes DB-only modules.
  uv run pytest tests/ -v --tb=short -m "not benchmark" \
    --cov=tapps_brain --cov-report=term-missing --cov-fail-under=80 \
    || fail "pytest"

  # TAP-511: explicit STRICT pass for tests/compat/ — silently skipping the
  # parity suite when TAPPS_BRAIN_DATABASE_URL is unset is what TAP-511
  # closed.  Run it again with STRICT=1 so a missing DSN at release time
  # fails the gate instead of being absorbed by the broader suite's
  # requires_postgres skip behavior.
  TAPPS_BRAIN_TESTS_STRICT=1 uv run pytest tests/compat/ -v --tb=short \
    || fail "compat suite under STRICT (TAP-511) — set TAPPS_BRAIN_DATABASE_URL"
fi

# TAP-1855: HTTP adapter warm-path benchmark gate.
# Off by default (SKIP_BENCHMARKS=1) to avoid adding 5-10 s to every release
# run.  Set SKIP_BENCHMARKS=0 to enable before performance-sensitive releases.
if [[ "$SKIP_BENCHMARKS" == "1" ]]; then
  echo "==> [5b/6] HTTP benchmark gate (skipped: SKIP_BENCHMARKS=1; set to 0 to enable)"
else
  echo "==> [5b/6] HTTP benchmark gate (tests/benchmarks/test_http_adapter_tools_list.py)"
  uv run pytest tests/benchmarks/test_http_adapter_tools_list.py -v --benchmark-only \
    --benchmark-fail=mean:1.5x \
    || fail "benchmark gate — p95/p99 regression; see docs/perf/benchmarks.md"
fi

if [[ "$SKIP_LINT" == "1" ]]; then
  echo "==> [6/6] Ruff + format + mypy (skipped: SKIP_LINT=1)"
else
  echo "==> [6/6] Ruff + format + mypy"
  uv run ruff check src/ tests/ || fail "ruff check"
  uv run ruff format --check src/ tests/ || fail "ruff format"
  uv run mypy --strict src/tapps_brain/ || fail "mypy"
fi

# TAP-570: optional cross-tenant HTTP denial smoke test (recommended pre-prod).
if [[ "$TAPPS_BRAIN_CROSS_TENANT_SMOKE" == "1" ]]; then
  echo "==> [9/9] Cross-tenant HTTP denial smoke test (TAP-570)"
  if [[ -z "${TAPPS_BRAIN_ADMIN_TOKEN:-}" || -z "${TAPPS_BRAIN_AUTH_TOKEN:-}" ]]; then
    fail "cross-tenant smoke requires TAPPS_BRAIN_ADMIN_TOKEN and TAPPS_BRAIN_AUTH_TOKEN"
  fi
  uv run pytest tests/integration/test_cross_tenant_http.py \
    -v --tb=short -s \
    || fail "cross-tenant HTTP smoke (TAP-570) — check sidecar at ${TAPPS_BRAIN_SIDECAR_URL:-http://localhost:8080}"
else
  echo "==> [9/9] Cross-tenant HTTP denial smoke (skipped: TAPPS_BRAIN_CROSS_TENANT_SMOKE != 1)"
  echo "    Set TAPPS_BRAIN_CROSS_TENANT_SMOKE=1 to run before production deployments."
fi

# Docs smoke gate: confirm the critical user-facing docs are present and the
# project still scans cleanly under the docsmcp scanner.  Cheap (< 2 s) and
# catches obvious regressions like accidental delete of the index or
# architecture artifacts.  The full drift / link / freshness audit runs from
# an agent via docs_check_drift + docs_release_gate.
if [[ "$TAPPS_BRAIN_DOCS_GATE" == "1" ]]; then
  echo "==> [10/10] Docs smoke gate (TAPPS_BRAIN_DOCS_GATE=1)"
  for doc in \
      README.md \
      CHANGELOG.md \
      docs/DOCUMENTATION_INDEX.md \
      docs/api-reference.md \
      docs/architecture.html \
      docs/engineering/system-architecture.md \
      docs/engineering/diagrams.md \
      docs/engineering/architecture-report.html \
      docs/engineering/call-flows.md \
      docs/engineering/data-stores-and-schema.md \
      llms.txt; do
    [[ -s "$doc" ]] || fail "docs gate — missing or empty: $doc"
  done
  if command -v docsmcp >/dev/null 2>&1; then
    docsmcp scan 2>&1 | tail -5 \
      || fail "docs gate — 'docsmcp scan' failed; check .docsmcp.yaml"
  else
    echo "    NOTE: docsmcp CLI not on PATH; skipping scan. Install with:"
    echo "          uv tool install --reinstall <path>/packages/docs-mcp"
  fi
else
  echo "==> [10/10] Docs smoke gate (skipped: TAPPS_BRAIN_DOCS_GATE != 1)"
  echo "    Set TAPPS_BRAIN_DOCS_GATE=1 to require critical docs to be present."
fi

echo "release-ready: OK (all stages passed)"
