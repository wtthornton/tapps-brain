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
  cp -p "$f" "$f.bak.$(date +%Y%m%d-%H%M%S)"
  sed -i "s/^BRAIN_VERSION=.*/BRAIN_VERSION=$VERSION/" "$f"
  echo "set    $f -> $(grep -E '^BRAIN_VERSION=' "$f")"
done

echo "done   BRAIN_VERSION=$VERSION"
