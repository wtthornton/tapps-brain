"""TAP-508 — OpenAPI contract gates.

These tests lock in the wire contract published at ``/openapi.json`` and
checked into ``docs/contracts/openapi.json``.  They guard against:

* dropping a route that consumers (tapps-mcp, AgentForge) depend on,
* removing the dual auth-scheme declarations,
* losing the standard tenant headers on data-plane / MCP routes,
* drifting the brain version out of ``importlib.metadata``.

The on-disk snapshot itself is verified by the CI job
``openapi-contract-drift`` (see ``.github/workflows/ci.yml``), which
re-runs ``scripts/snapshot_openapi.py`` and ``git diff --exit-code``s
the result against the checked-in file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tapps_brain.http_adapter import create_app
from tapps_brain.openapi_contract import (
    _bundled_schema_version,
    _service_version,
    build_openapi_spec,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO_ROOT / "docs" / "contracts" / "openapi.json"


@pytest.fixture(scope="module")
def spec() -> dict:
    """Build a fresh spec for the module — no DB, no lifespan."""
    app = create_app()
    return build_openapi_spec(app)


# ---------------------------------------------------------------------------
# Coverage: every public route documented
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/ready",
        "/metrics",
        "/info",
        "/snapshot",
        "/mcp",
        "/v1/remember",
        "/v1/reinforce",
        "/v1/remember:batch",
        "/v1/recall:batch",
        "/v1/reinforce:batch",
        "/admin/projects",
        "/admin/projects/{project_id}",
    ],
)
def test_path_present(spec: dict, path: str) -> None:
    assert path in spec["paths"], f"missing path in OpenAPI: {path}"


# ---------------------------------------------------------------------------
# Security schemes
# ---------------------------------------------------------------------------


def test_dual_security_schemes(spec: dict) -> None:
    schemes = spec["components"]["securitySchemes"]
    assert "bearerAuth" in schemes
    assert "adminBearerAuth" in schemes
    assert schemes["bearerAuth"]["scheme"] == "bearer"
    assert schemes["adminBearerAuth"]["scheme"] == "bearer"


def test_admin_routes_use_admin_scheme(spec: dict) -> None:
    op = spec["paths"]["/admin/projects"]["get"]
    assert {"adminBearerAuth": []} in op["security"]


def test_data_plane_routes_use_data_plane_scheme(spec: dict) -> None:
    op = spec["paths"]["/v1/remember"]["post"]
    assert {"bearerAuth": []} in op["security"]


# ---------------------------------------------------------------------------
# Tenant headers
# ---------------------------------------------------------------------------


def _param_names(op: dict) -> set[str]:
    names: set[str] = set()
    for p in op.get("parameters", []):
        if "$ref" in p:
            names.add(p["$ref"].rsplit("/", 1)[-1])
        elif "name" in p:
            names.add(p["name"])
    return names


def test_v1_routes_carry_tenant_headers(spec: dict) -> None:
    op = spec["paths"]["/v1/remember"]["post"]
    names = _param_names(op)
    assert "XProjectId" in names
    assert "XAgentId" in names


def test_write_routes_advertise_idempotency_key(spec: dict) -> None:
    for path in ("/v1/remember", "/v1/reinforce", "/v1/remember:batch"):
        op = spec["paths"][path]["post"]
        assert "XIdempotencyKey" in _param_names(op), f"{path} must advertise X-Idempotency-Key"


def test_mcp_route_documented(spec: dict) -> None:
    mcp = spec["paths"]["/mcp"]["post"]
    names = _param_names(mcp)
    assert "XProjectId" in names
    assert {"bearerAuth": []} in mcp["security"]


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------


def test_error_envelope_schema_present(spec: dict) -> None:
    schema = spec["components"]["schemas"]["Error"]
    assert "error" in schema["required"]
    assert schema["properties"]["error"]["type"] == "string"


def test_protected_routes_document_401_and_403(spec: dict) -> None:
    op = spec["paths"]["/v1/remember"]["post"]
    assert "401" in op["responses"]
    assert "403" in op["responses"]


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------


def test_spec_version_matches_importlib_metadata(spec: dict) -> None:
    assert spec["info"]["version"] == _service_version()


def test_schema_version_extension_present(spec: dict) -> None:
    sv = spec["info"].get("x-schema-version")
    assert isinstance(sv, int)
    assert sv == _bundled_schema_version()
    assert sv > 0


# ---------------------------------------------------------------------------
# Snapshot drift — also covered in CI; this one is a fast local guard.
# ---------------------------------------------------------------------------


def test_runtime_spec_matches_checked_in_snapshot(spec: dict) -> None:
    """Belt-and-suspenders for the CI drift gate.

    If you intentionally changed the wire contract, run::

        uv run python scripts/snapshot_openapi.py

    and commit ``docs/contracts/openapi.json``.
    """
    assert SNAPSHOT.exists(), f"missing {SNAPSHOT}; run scripts/snapshot_openapi.py and commit it"
    on_disk = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert spec == on_disk, (
        "OpenAPI spec drift — run scripts/snapshot_openapi.py and commit "
        "docs/contracts/openapi.json"
    )
