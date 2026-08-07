#!/usr/bin/env bash
#
# Sync BRAIN_VERSION in docker/.env and docker/.env.example to pyproject.toml.
#
#   bash scripts/set-brain-version.sh            # take version from pyproject
#   bash scripts/set-brain-version.sh 3.31.0     # or pass it explicitly
#
# Exists so an agent can align the deploy pin WITHOUT being granted read access
# to docker/.env. It rewrites exactly one line and prints only that line back —
# no other key in the file is read, printed, or modified.
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
  VERSION="$(grep -E '^version[[:space:]]*=' pyproject.toml | head -1 | sed 's/.*=[[:space:]]*"\(.*\)"/\1/')"
fi

if ! printf '%s' "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.]+)?$'; then
  echo "ERROR: '$VERSION' is not a semver version" >&2
  exit 1
fi

for f in docker/.env docker/.env.example; do
  if [ ! -f "$f" ]; then
    echo "skip   $f (absent)"
    continue
  fi
  if ! grep -qE '^BRAIN_VERSION=' "$f"; then
    echo "ERROR: $f has no BRAIN_VERSION line" >&2
    exit 1
  fi
  # Back up ONLY untracked files. docker/.env holds live secrets and is
  # gitignored, so a local copy is its only rollback — but the copy must stay
  # gitignored too (see .gitignore `.env.bak.*`; the pre-existing
  # `*.env.*.bak` rule does not match this name shape and left secret backups
  # as untracked files a `git add -A` would have committed).
  # docker/.env.example is tracked, so git history is already its backup and an
  # extra .bak is untracked noise.
  if ! git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    cp -p "$f" "$f.bak.$(date +%Y%m%d-%H%M%S)"
  fi
  sed -i "s/^BRAIN_VERSION=.*/BRAIN_VERSION=$VERSION/" "$f"
  echo "set    $f -> $(grep -E '^BRAIN_VERSION=' "$f")"
done

echo "done   BRAIN_VERSION=$VERSION"

# ---------------------------------------------------------------------------
# Release-artifact staleness guard.
#
# The OpenAPI snapshot and llms*.txt EMBED the package version, so a bump that
# does not regenerate them fails three tests — but only on the push-to-main CI
# run, long after the bump looked successful:
#   test_openapi_contract.py::test_runtime_spec_matches_checked_in_snapshot
#   test_release_artifacts.py::test_openapi_snapshot_for_current_version_exists
#   test_release_artifacts.py::test_llms_txt_version_matches_pyproject
# This shipped a red main on 3.31.0 and 3.31.1. Report it here, at the moment
# of the bump, with the exact commands — rather than letting CI find it.
#
# Warn rather than fail: this script is also used to re-pin a deploy without a
# version change, where regenerating artifacts is not wanted.
# ---------------------------------------------------------------------------
stale=""
[ -f "docs/contracts/openapi-$VERSION.json" ] || stale="$stale\n    docs/contracts/openapi-$VERSION.json (missing)"
for f in llms.txt llms-full.txt; do
  if [ -f "$f" ] && ! grep -qE "^- Version: $VERSION\$" "$f"; then
    stale="$stale\n    $f (declares $(grep -E '^- Version:' "$f" | head -1 | sed 's/^- Version: //'))"
  fi
done

if [ -n "$stale" ]; then
  echo
  echo "WARNING: release artifacts are stale for $VERSION:"
  printf '%b\n' "$stale"
  echo
  echo "  Regenerate before committing, or main CI will go red:"
  echo "    uv run python scripts/snapshot_openapi.py"
  echo "    # llms.txt + llms-full.txt: docs_generate_llms_txt (docs-mcp), modes compact and full"
  echo
fi
