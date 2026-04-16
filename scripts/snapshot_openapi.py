#!/usr/bin/env python3
"""Snapshot the tapps-brain OpenAPI spec to ``docs/contracts/``.

Imports the FastAPI app, generates the spec via the enriched
``app.openapi()``, and writes:

* ``docs/contracts/openapi.json``                   — current/HEAD
* ``docs/contracts/openapi-<brain-version>.json``   — version-pinned

CI re-runs this script and fails on ``git diff --exit-code`` so any
wire-affecting change forces an explicit spec update (TAP-508).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "docs" / "contracts"


def _build_app_for_snapshot() -> object:
    """Build the FastAPI app without starting its lifespan or DB pool."""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from tapps_brain.http_adapter import create_app

    return create_app()


def main() -> int:
    app = _build_app_for_snapshot()
    spec = app.openapi()  # type: ignore[attr-defined]
    version = spec.get("info", {}).get("version", "unknown")

    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)

    head_path = CONTRACTS_DIR / "openapi.json"
    version_path = CONTRACTS_DIR / f"openapi-{version}.json"

    serialized = json.dumps(spec, indent=2, sort_keys=True) + "\n"
    head_path.write_text(serialized, encoding="utf-8")
    version_path.write_text(serialized, encoding="utf-8")

    print(f"wrote {head_path.relative_to(REPO_ROOT)}")
    print(f"wrote {version_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
