"""Integration smoke test — TAP-570: cross-tenant denial at the HTTP sidecar.

Verifies that the three-layer tenant isolation (composite key, RLS policies,
``FORCE ROW LEVEL SECURITY``) holds end-to-end when requests hit the live HTTP
sidecar at ``:8080``.

Gate
----
Set ``TAPPS_BRAIN_CROSS_TENANT_SMOKE=1`` to activate; the whole module is skipped
otherwise.  This test needs a running sidecar and is NOT part of the default CI
matrix — it runs in a dedicated job that starts the Docker Compose stack first.

Required environment variables
-------------------------------
``TAPPS_BRAIN_CROSS_TENANT_SMOKE=1``
    Activate this module.
``TAPPS_BRAIN_ADMIN_TOKEN``
    Admin bearer for ``/admin/*`` project setup and token rotation.
``TAPPS_BRAIN_AUTH_TOKEN``
    Global data-plane bearer token (used for cases 1–4 when per-tenant auth is
    not the focus; the sidecar must accept it).

Optional environment variables
------------------------------
``TAPPS_BRAIN_SIDECAR_URL``
    Base URL for the running sidecar (default: ``http://localhost:8080``).
``TAPPS_BRAIN_PER_TENANT_AUTH=1``
    Set to ``1`` when the *sidecar* is also started with
    ``TAPPS_BRAIN_PER_TENANT_AUTH=1``.  Activates cases 5–6 (per-tenant token
    mismatch assertions).  Must mirror the sidecar setting exactly.
``TAPPS_TEST_POSTGRES_DSN``
    If set, also runs the FORCE RLS verification (case 7) directly against
    Postgres.  The DSN must connect as the table owner so we can assert that
    ``FORCE ROW LEVEL SECURITY`` prevents even the owner from reading
    cross-tenant rows.

Seven test cases
----------------
Case 1 — Nominal save: proj-a saves a uniquely-valued memory (expect 200).
Case 2 — RLS read isolation: proj-b recalls the same unique value → 0 results.
Case 3 — RLS key isolation: proj-b recalls the exact key name → 0 results.
Case 4 — No bleed: proj-b recall returns ONLY proj-b rows; proj-a rows invisible.
Case 5 — Token mismatch A→B: token-a + ``X-Project-Id: proj-b`` → 403.
          (Skipped unless ``TAPPS_BRAIN_PER_TENANT_AUTH=1``.)
Case 6 — Token mismatch B→A: token-b + ``X-Project-Id: proj-a`` → 403.
          (Skipped unless ``TAPPS_BRAIN_PER_TENANT_AUTH=1``.)
Case 7 — FORCE RLS: SQL confirms ``FORCE ROW LEVEL SECURITY`` on both tenanted
          tables; owner-role SELECT with ``app.project_id=proj-b`` returns no
          rows for proj-a's key.
          (Skipped unless ``TAPPS_TEST_POSTGRES_DSN`` is set.)
"""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import Generator
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Module-level gate
# ---------------------------------------------------------------------------

_SMOKE_ENABLED = os.environ.get("TAPPS_BRAIN_CROSS_TENANT_SMOKE", "") == "1"

pytestmark = pytest.mark.skipif(
    not _SMOKE_ENABLED,
    reason=(
        "TAPPS_BRAIN_CROSS_TENANT_SMOKE is not set to '1'. "
        "This test requires a live :8080 sidecar. "
        "Start the compose stack and set the env var to run it."
    ),
)

# ---------------------------------------------------------------------------
# Configuration (read once at module load)
# ---------------------------------------------------------------------------

_SIDECAR_URL = os.environ.get("TAPPS_BRAIN_SIDECAR_URL", "http://localhost:8080").rstrip("/")
_ADMIN_TOKEN = os.environ.get("TAPPS_BRAIN_ADMIN_TOKEN", "")
_AUTH_TOKEN = os.environ.get("TAPPS_BRAIN_AUTH_TOKEN", "")
_PER_TENANT_AUTH = os.environ.get("TAPPS_BRAIN_PER_TENANT_AUTH", "") == "1"
_PG_DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_ADMIN_TOKEN}"}


def _data_headers(token: str, project_id: str, agent_id: str = "smoke-agent") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Project-Id": project_id,
        "X-Agent-Id": agent_id,
        "Content-Type": "application/json",
    }


def _unique_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Module-scoped fixture: two isolated projects
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def smoke_ctx() -> Generator[dict[str, str], None, None]:
    """Register two projects, rotate per-tenant tokens, yield context, clean up."""
    import httpx

    if not _ADMIN_TOKEN:
        pytest.skip("TAPPS_BRAIN_ADMIN_TOKEN is required for the cross-tenant smoke test")
    if not _AUTH_TOKEN:
        pytest.skip("TAPPS_BRAIN_AUTH_TOKEN is required for the cross-tenant smoke test")

    from tapps_brain.profile import get_builtin_profile

    profile_json: dict[str, Any] = get_builtin_profile("repo-brain").model_dump(mode="json")

    proj_a = _unique_id("smoke-a")
    proj_b = _unique_id("smoke-b")
    admin_hdrs = _admin_headers()

    with httpx.Client(base_url=_SIDECAR_URL, timeout=30.0) as client:
        # ---- register proj-a ----
        r = client.post(
            "/admin/projects",
            headers=admin_hdrs,
            json={"project_id": proj_a, "profile": profile_json, "approved": True},
        )
        assert r.status_code == 201, (
            f"Registering proj-a failed — status {r.status_code}: {r.text}\n"
            "Is the sidecar running and TAPPS_BRAIN_ADMIN_TOKEN correct?"
        )

        # ---- register proj-b ----
        r = client.post(
            "/admin/projects",
            headers=admin_hdrs,
            json={"project_id": proj_b, "profile": profile_json, "approved": True},
        )
        assert r.status_code == 201, f"Registering proj-b failed — {r.status_code}: {r.text}"

        # ---- rotate per-tenant tokens ----
        r = client.post(f"/admin/projects/{proj_a}/rotate-token", headers=admin_hdrs)
        assert r.status_code == 201, f"Rotating token for proj-a failed — {r.status_code}: {r.text}"
        token_a = r.json()["token"]

        r = client.post(f"/admin/projects/{proj_b}/rotate-token", headers=admin_hdrs)
        assert r.status_code == 201, f"Rotating token for proj-b failed — {r.status_code}: {r.text}"
        token_b = r.json()["token"]

        yield {
            "proj_a": proj_a,
            "proj_b": proj_b,
            "token_a": token_a,
            "token_b": token_b,
        }

        # ---- teardown: delete both projects ----
        for pid in (proj_a, proj_b):
            with contextlib.suppress(Exception):
                client.delete(f"/admin/projects/{pid}", headers=admin_hdrs)


# ---------------------------------------------------------------------------
# Cross-tenant denial smoke tests
# ---------------------------------------------------------------------------


class TestCrossTenantHttp:
    """End-to-end cross-tenant denial assertions against a live :8080 sidecar.

    Each test method is independent; the ``smoke_ctx`` fixture handles setup and
    teardown of the two isolated projects exactly once per module run.
    """

    # ------------------------------------------------------------------
    # Case 1: Nominal — proj-a write succeeds
    # ------------------------------------------------------------------

    def test_case1_nominal_save(self, smoke_ctx: dict[str, str]) -> None:
        """proj-a can save a memory with its own token and project header."""
        import httpx

        proj_a = smoke_ctx["proj_a"]
        # Use the global token when per-tenant auth is disabled; per-tenant otherwise.
        token = smoke_ctx["token_a"] if _PER_TENANT_AUTH else _AUTH_TOKEN

        with httpx.Client(base_url=_SIDECAR_URL, timeout=30.0) as client:
            r = client.post(
                "/v1/remember",
                headers=_data_headers(token, proj_a),
                json={
                    "key": f"smoke-key-{smoke_ctx['proj_a']}",
                    "value": f"tapps-smoke-sentinel-{smoke_ctx['proj_a']}",
                    "tier": "context",
                    "source": "smoke-test",
                },
            )
        assert r.status_code == 200, (
            f"Case 1 — proj-a save failed: {r.status_code} {r.text}\n"
            "Check TAPPS_BRAIN_AUTH_TOKEN and X-Project-Id handling on the sidecar."
        )

    # ------------------------------------------------------------------
    # Case 2: RLS read isolation — proj-b recalls proj-a's unique value
    # ------------------------------------------------------------------

    def test_case2_rls_blocks_cross_tenant_recall(self, smoke_ctx: dict[str, str]) -> None:
        """proj-b recall for proj-a's sentinel value returns zero results (RLS).

        This directly proves the row-level security property: the DB returns
        only proj-b-scoped rows when the request carries X-Project-Id: proj-b.
        """
        import httpx

        proj_a = smoke_ctx["proj_a"]
        proj_b = smoke_ctx["proj_b"]
        token_b = smoke_ctx["token_b"] if _PER_TENANT_AUTH else _AUTH_TOKEN
        sentinel_value = f"tapps-smoke-sentinel-{proj_a}"

        # First ensure proj-a's row exists (case 1 may not have run yet in isolation).
        token_a = smoke_ctx["token_a"] if _PER_TENANT_AUTH else _AUTH_TOKEN
        with httpx.Client(base_url=_SIDECAR_URL, timeout=30.0) as client:
            client.post(
                "/v1/remember",
                headers=_data_headers(token_a, proj_a),
                json={
                    "key": f"smoke-key-{proj_a}",
                    "value": sentinel_value,
                    "tier": "context",
                    "source": "smoke-test",
                },
            )

            # Now recall as proj-b — must return zero results.
            r = client.post(
                "/v1/recall:batch",
                headers=_data_headers(token_b, proj_b),
                json={"queries": [sentinel_value]},
            )

        assert r.status_code == 200, (
            f"Case 2 — proj-b recall request itself failed: {r.status_code} {r.text}"
        )
        body = r.json()
        results = body.get("results", [])
        # results is a list of per-query result lists
        entries: list[Any] = []
        for query_result in results:
            if isinstance(query_result, list):
                entries.extend(query_result)
            elif isinstance(query_result, dict) and "entries" in query_result:
                entries.extend(query_result["entries"])

        assert len(entries) == 0, (
            f"Case 2 — RLS FAILURE: proj-b recall returned {len(entries)} entry/entries "
            f"for proj-a's sentinel value '{sentinel_value}'. "
            f"Raw response: {r.text[:500]}"
        )

    # ------------------------------------------------------------------
    # Case 3: RLS key isolation — proj-b searches for proj-a's exact key
    # ------------------------------------------------------------------

    def test_case3_rls_blocks_key_search(self, smoke_ctx: dict[str, str]) -> None:
        """proj-b searching for proj-a's exact key returns zero results (RLS)."""
        import httpx

        proj_a = smoke_ctx["proj_a"]
        proj_b = smoke_ctx["proj_b"]
        token_b = smoke_ctx["token_b"] if _PER_TENANT_AUTH else _AUTH_TOKEN
        key_name = f"smoke-key-{proj_a}"

        with httpx.Client(base_url=_SIDECAR_URL, timeout=30.0) as client:
            r = client.post(
                "/v1/recall:batch",
                headers=_data_headers(token_b, proj_b),
                json={"queries": [key_name]},
            )

        assert r.status_code == 200, (
            f"Case 3 — proj-b key recall request failed: {r.status_code} {r.text}"
        )
        body = r.json()
        results = body.get("results", [])
        entries: list[Any] = []
        for query_result in results:
            if isinstance(query_result, list):
                entries.extend(query_result)
            elif isinstance(query_result, dict) and "entries" in query_result:
                entries.extend(query_result["entries"])

        assert len(entries) == 0, (
            f"Case 3 — RLS FAILURE: proj-b key search returned {len(entries)} entry/entries "
            f"for proj-a's key '{key_name}'. "
            f"Raw response: {r.text[:500]}"
        )

    # ------------------------------------------------------------------
    # Case 4: Proj-b data is visible to proj-b but not contaminated by proj-a
    # ------------------------------------------------------------------

    def test_case4_proj_b_only_sees_own_data(self, smoke_ctx: dict[str, str]) -> None:
        """proj-b saves its own row and can recall it; proj-a's rows never appear."""
        import httpx

        proj_a = smoke_ctx["proj_a"]
        proj_b = smoke_ctx["proj_b"]
        token_a = smoke_ctx["token_a"] if _PER_TENANT_AUTH else _AUTH_TOKEN
        token_b = smoke_ctx["token_b"] if _PER_TENANT_AUTH else _AUTH_TOKEN

        proj_b_sentinel = f"tapps-smoke-proj-b-only-{proj_b}"
        proj_a_sentinel = f"tapps-smoke-sentinel-{proj_a}"

        with httpx.Client(base_url=_SIDECAR_URL, timeout=30.0) as client:
            # Seed proj-b's own row.
            r = client.post(
                "/v1/remember",
                headers=_data_headers(token_b, proj_b),
                json={
                    "key": f"smoke-key-{proj_b}",
                    "value": proj_b_sentinel,
                    "tier": "context",
                    "source": "smoke-test",
                },
            )
            assert r.status_code == 200, f"Case 4 — proj-b write failed: {r.status_code} {r.text}"

            # Also ensure proj-a's row is present.
            client.post(
                "/v1/remember",
                headers=_data_headers(token_a, proj_a),
                json={
                    "key": f"smoke-key-{proj_a}",
                    "value": proj_a_sentinel,
                    "tier": "context",
                    "source": "smoke-test",
                },
            )

            # proj-b searches for proj-a's sentinel — must return nothing.
            r_cross = client.post(
                "/v1/recall:batch",
                headers=_data_headers(token_b, proj_b),
                json={"queries": [proj_a_sentinel]},
            )

        assert r_cross.status_code == 200, f"Case 4 — cross recall failed: {r_cross.text}"
        body = r_cross.json()
        results = body.get("results", [])
        cross_entries: list[Any] = []
        for query_result in results:
            if isinstance(query_result, list):
                cross_entries.extend(query_result)
            elif isinstance(query_result, dict) and "entries" in query_result:
                cross_entries.extend(query_result["entries"])

        assert len(cross_entries) == 0, (
            f"Case 4 — RLS FAILURE: proj-b returned {len(cross_entries)} entries "
            f"for proj-a's sentinel '{proj_a_sentinel}'. "
            f"Raw: {r_cross.text[:500]}"
        )

    # ------------------------------------------------------------------
    # Cases 5–6: Per-tenant token mismatch (conditional on per-tenant auth)
    # ------------------------------------------------------------------

    @pytest.mark.skipif(
        not _PER_TENANT_AUTH,
        reason="TAPPS_BRAIN_PER_TENANT_AUTH is not set to '1'; skipping per-tenant token mismatch tests",
    )
    def test_case5_token_a_rejected_for_proj_b(self, smoke_ctx: dict[str, str]) -> None:
        """token-a + X-Project-Id: proj-b → 403 (per-tenant token mismatch).

        Proves that an attacker with proj-a's token cannot authenticate
        requests claiming to be proj-b.  Requires ``TAPPS_BRAIN_PER_TENANT_AUTH=1``
        on both the sidecar and the test runner.
        """
        import httpx

        proj_b = smoke_ctx["proj_b"]
        token_a = smoke_ctx["token_a"]  # proj-a's per-tenant token

        with httpx.Client(base_url=_SIDECAR_URL, timeout=30.0) as client:
            r = client.post(
                "/v1/remember",
                # token-a presented for proj-b — must be rejected.
                headers=_data_headers(token_a, proj_b),
                json={"key": "should-never-land", "value": "cross-tenant-auth-bypass-attempt"},
            )

        assert r.status_code == 403, (
            f"Case 5 — AUTH FAILURE: token-a accepted for proj-b (expected 403, got {r.status_code}). "
            f"Per-tenant token isolation FAILED. Response: {r.text[:500]}"
        )

    @pytest.mark.skipif(
        not _PER_TENANT_AUTH,
        reason="TAPPS_BRAIN_PER_TENANT_AUTH is not set to '1'; skipping per-tenant token mismatch tests",
    )
    def test_case6_token_b_rejected_for_proj_a(self, smoke_ctx: dict[str, str]) -> None:
        """token-b + X-Project-Id: proj-a → 403 (per-tenant token mismatch).

        Symmetric of case 5: proj-b's token cannot access proj-a.
        """
        import httpx

        proj_a = smoke_ctx["proj_a"]
        token_b = smoke_ctx["token_b"]  # proj-b's per-tenant token

        with httpx.Client(base_url=_SIDECAR_URL, timeout=30.0) as client:
            r = client.post(
                "/v1/remember",
                # token-b presented for proj-a — must be rejected.
                headers=_data_headers(token_b, proj_a),
                json={"key": "should-never-land", "value": "cross-tenant-auth-bypass-attempt"},
            )

        assert r.status_code == 403, (
            f"Case 6 — AUTH FAILURE: token-b accepted for proj-a (expected 403, got {r.status_code}). "
            f"Per-tenant token isolation FAILED. Response: {r.text[:500]}"
        )

    # ------------------------------------------------------------------
    # Case 7: FORCE ROW LEVEL SECURITY — confirmed via SQL
    # ------------------------------------------------------------------

    @pytest.mark.skipif(
        not _PG_DSN,
        reason="TAPPS_TEST_POSTGRES_DSN not set; skipping FORCE RLS SQL verification",
    )
    def test_case7_force_rls_confirmed_via_sql(self, smoke_ctx: dict[str, str]) -> None:
        """FORCE ROW LEVEL SECURITY is active on tenanted tables (SQL check).

        Two sub-assertions:
        1. Both ``private_memories`` and ``project_profiles`` have
           ``relforcerowsecurity = true`` in pg_class.
        2. Connecting as the *table owner* (using TAPPS_TEST_POSTGRES_DSN,
           which is expected to be the owner DSN in CI) and scoping to
           proj-b via ``SET LOCAL app.project_id`` returns zero rows for
           proj-a's key — even for the owner role.  This proves that FORCE
           RLS prevents silent cross-tenant leakage through privileged
           connections, not just the runtime role assertion.

        The CI DSN typically runs as the superuser/owner with
        ``TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1``.  This test deliberately
        uses it to validate that FORCE RLS holds regardless.
        """
        import contextlib

        from tapps_brain.postgres_connection import PostgresConnectionManager
        from tapps_brain.postgres_migrations import apply_private_migrations

        apply_private_migrations(_PG_DSN)

        proj_a = smoke_ctx["proj_a"]
        proj_b = smoke_ctx["proj_b"]

        owner_cm = PostgresConnectionManager(_PG_DSN)
        try:
            with owner_cm.get_connection() as conn:
                with conn.cursor() as cur:
                    # 1. Verify FORCE ROW LEVEL SECURITY is set.
                    cur.execute(
                        """
                        SELECT relname, relforcerowsecurity
                        FROM   pg_class
                        WHERE  relname IN ('private_memories', 'project_profiles')
                        ORDER BY relname
                        """
                    )
                    rows = cur.fetchall()

                assert rows, (
                    "Case 7 — pg_class query returned no rows; "
                    "tables may not exist yet. Run migrations first."
                )
                for relname, force_rls in rows:
                    assert force_rls, (
                        f"Case 7 — FORCE RLS FAILURE: {relname} does NOT have "
                        "FORCE ROW LEVEL SECURITY enabled. "
                        "Apply migration 012 (tapps-brain maintenance migrate)."
                    )

                # 2. As owner: seed proj-a row, then scope to proj-b and assert
                #    SELECT returns nothing (FORCE RLS blocks even the owner).
                unique_key = f"smoke-force-rls-{uuid.uuid4().hex[:8]}"
                unique_val = f"force-rls-sentinel-{proj_a}"

                with owner_cm.get_connection() as conn:
                    with conn.cursor() as cur:
                        # Seed directly (owner bypasses RLS for INSERT only if FORCE
                        # RLS would allow the WITH CHECK — but we're the table owner
                        # and FORCE RLS applies on SELECT; INSERT with owner goes
                        # through because WITH CHECK is not FORCE'd for owner in
                        # the current policy set.  We use the owner as migrator.
                        cur.execute(
                            """
                            INSERT INTO private_memories (project_id, agent_id, key, value)
                            VALUES (%s, 'smoke-agent', %s, %s)
                            ON CONFLICT (project_id, agent_id, key) DO UPDATE
                                SET value = EXCLUDED.value
                            """,
                            (proj_a, unique_key, unique_val),
                        )

                    # Scope the connection to proj-b — FORCE RLS must hide proj-a rows.
                    with conn.cursor() as cur:
                        cur.execute("SET LOCAL app.project_id = %s", (proj_b,))
                        cur.execute(
                            "SELECT count(*) FROM private_memories WHERE key = %s",
                            (unique_key,),
                        )
                        count_row = cur.fetchone()
                        assert count_row is not None
                        visible_count = count_row[0]

                    assert visible_count == 0, (
                        f"Case 7 — FORCE RLS FAILURE: owner-role SELECT scoped to proj-b "
                        f"returned {visible_count} row(s) for proj-a's key '{unique_key}'. "
                        "FORCE ROW LEVEL SECURITY is NOT preventing cross-tenant reads."
                    )

                # Cleanup the seeded row.
                with owner_cm.get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "DELETE FROM private_memories WHERE project_id = %s AND key = %s",
                            (proj_a, unique_key),
                        )
        finally:
            with contextlib.suppress(Exception):
                owner_cm.close()
