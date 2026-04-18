#!/usr/bin/env bash
# Production release gate: docs, packaging, Python QA, OpenClaw plugin.
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
SKIP_LINT="${SKIP_LINT:-0}"
# TAP-570: set TAPPS_BRAIN_CROSS_TENANT_SMOKE=1 to include the cross-tenant HTTP
# denial smoke test.  Requires a live sidecar on TAPPS_BRAIN_SIDECAR_URL (default
# http://localhost:8080) with TAPPS_BRAIN_ADMIN_TOKEN + TAPPS_BRAIN_AUTH_TOKEN set.
# Strongly recommended before any production deployment that touches RLS, the HTTP
# adapter, or per-tenant auth.  Off by default to avoid blocking release gates in
# environments without a running sidecar.
TAPPS_BRAIN_CROSS_TENANT_SMOKE="${TAPPS_BRAIN_CROSS_TENANT_SMOKE:-0}"

fail() {
  echo "release-ready: FAILED — $*" >&2
  echo "Remediation: scripts/publish-checklist.md, docs/guides/openclaw-runbook.md, EPIC-036" >&2
  exit 1
}

need_uv() {
  command -v uv >/dev/null 2>&1 || fail "uv not found (install: https://github.com/astral-sh/uv)"
}

need_node() {
  command -v npm >/dev/null 2>&1 || fail "npm not found (Node 18+ required for openclaw-plugin)"
}

echo "==> [1/8] OpenClaw docs consistency"
python3 scripts/check_openclaw_docs_consistency.py || fail "docs consistency checker"

echo "==> [2/8] uv sync --group dev"
need_uv
uv sync --group dev

echo "==> [3/8] Packaging build (clean dist/)"
rm -rf dist/
uv build

echo "==> [4/8] Wheel smoke install + import"
uv venv .venv-release-smoke --clear
# shellcheck disable=SC2035
uv pip install --python .venv-release-smoke dist/*.whl
if [[ -x .venv-release-smoke/bin/python ]]; then
  .venv-release-smoke/bin/python -c "from tapps_brain import __version__; print(f'import ok: tapps_brain {__version__}')"
else
  fail "expected .venv-release-smoke/bin/python (use WSL/Git Bash on Windows)"
fi

echo "==> [5/8] Version consistency tests"
uv run pytest tests/unit/test_version_consistency.py -v --tb=short || fail "version consistency"

if [[ "$SKIP_FULL_PYTEST" == "1" ]]; then
  echo "==> [6/8] Full pytest suite (skipped: SKIP_FULL_PYTEST=1)"
else
  echo "==> [6/8] Full pytest suite (no benchmarks, coverage gate)"
  uv run pytest tests/ -v --tb=short -m "not benchmark" \
    --cov=tapps_brain --cov-report=term-missing --cov-fail-under=88 \
    || fail "pytest"

  # TAP-511: explicit STRICT pass for tests/compat/ — silently skipping the
  # parity suite when TAPPS_BRAIN_DATABASE_URL is unset is what TAP-511
  # closed.  Run it again with STRICT=1 so a missing DSN at release time
  # fails the gate instead of being absorbed by the broader suite's
  # requires_postgres skip behavior.
  TAPPS_BRAIN_TESTS_STRICT=1 uv run pytest tests/compat/ -v --tb=short \
    || fail "compat suite under STRICT (TAP-511) — set TAPPS_BRAIN_DATABASE_URL"
fi

if [[ "$SKIP_LINT" == "1" ]]; then
  echo "==> [7/8] Ruff + format + mypy (skipped: SKIP_LINT=1)"
else
  echo "==> [7/8] Ruff + format + mypy"
  uv run ruff check src/ tests/ || fail "ruff check"
  uv run ruff format --check src/ tests/ || fail "ruff format"
  uv run mypy --strict src/tapps_brain/ || fail "mypy"
fi

echo "==> [8/8] OpenClaw plugin (npm ci, build, test)"
need_node
(
  cd openclaw-plugin
  npm ci
  npm run build
  npm test
) || fail "openclaw-plugin"

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

echo "release-ready: OK (all stages passed)"
