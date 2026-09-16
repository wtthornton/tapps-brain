"""Tenancy isolation + sharing matrix, driven against the HTTP app (L4, VAL-10).

One file proves, in a single run against a real (throwaway, ephemeral) Postgres,
that private-memory isolation, Hive group-scoped sharing, and the project-local
``memory_group`` partition all coexist correctly under
``TAPPS_BRAIN_STRICT_PROJECTS=1`` + ``TAPPS_BRAIN_STRICT_AGENT_ID=1``:

* **(a) Private isolation** — REST ``/v1/remember`` + ``/v1/recall``.  Agent
  ``x`` writes in ``proj-a``; the same agent recalling from ``proj-b`` must
  not see it, recalling from ``proj-a`` must.

* **(b) Group-scoped Hive sharing** — the real cross-agent, cross-project
  mechanism.  ``x`` (member of Hive group ``g`` under ``proj-a``) saves
  privately (MCP ``memory_save``), then explicitly propagates via MCP
  ``hive_propagate(agent_scope="group:<g>")``, which
  :class:`~tapps_brain.backends.PropagationEngine` routes into
  ``hive_memories`` under namespace ``g`` after checking ``x``'s real
  membership (``hive_store.agent_is_group_member``, a ``hive_group_members``
  read).  ``y`` (member of ``g`` under
  ``proj-b``) reads it back via the MCP ``hive_search`` tool, which resolves
  the searcher's allowed namespaces from ``hive_group_members`` at query time
  (:meth:`PostgresHiveBackend.get_agent_groups`) — **not** from a namespace
  string the caller supplies.  ``z`` (registered in ``proj-a``, never added to
  ``g``) must not see it.  See "The producer" below for why this — and not
  the project-local ``memory_group`` tag-widening path (TAP-6695) — is the
  predicate that actually decides cross-project Hive visibility.

* **(c) ``memory_group`` label inside one project** — the project-local
  partition column (:mod:`tapps_brain.memory_group`), unrelated to Hive.  Two
  labels (``g``, ``h``) coexist in ``proj-a``; the MCP ``memory_search`` tool's
  ``group=`` filter returns only the requested label, and the same filter in
  ``proj-b`` returns nothing (it is a project-local column, not shared).

Two guard tests prove the matrix runs under the strict-tenancy flags: a
headerless write is refused with the L2 (TAP-7243) envelope, whatever axis
fires first.

The producer (read before trusting a namespace string)
--------------------------------------------------------
``git grep -n "hive_group_members" src/tapps_brain`` shows the membership
table is read in exactly two places that matter for a *recall*:

1. ``QueryMixin._recall_group_tags`` (``src/tapps_brain/_store_query.py``) —
   widens the **private_memories** query to admit rows tagged
   ``scope:group:<name>`` for a group the agent belongs to.  This predicate
   never leaves ``project_id = %s`` (see
   ``tests/test_group_scoped_recall.py::TestGroupMembershipIsProjectScoped``),
   so it cannot be the case (b) mechanism here: (b) requires ``y`` (``proj-b``)
   to see a row ``x`` wrote under ``proj-a``.

2. ``PostgresHiveBackend.get_agent_groups(agent_id, project_id)``
   (``src/tapps_brain/postgres_hive.py``), called by
   ``HiveBackend.search_with_groups`` — via
   ``services/hive_service.py::hive_search`` (the MCP ``hive_search`` tool) —
   to compute the caller's allowed Hive **namespaces** at query time.  A row
   written to ``hive_memories`` under namespace ``g`` is visible to a caller
   only when ``get_agent_groups(caller, caller's project)`` returns ``"g"`` —
   a live, per-request read of ``hive_group_members``, not a namespace string
   the caller asserts or a config declared once at store construction
   (``MemoryStore._groups`` / ``_append_group_memories``, which is
   process-static and never wired into any REST or MCP recall path in this
   tree — dead for this purpose, not exercised here).

   This is the trap the brief warns about: it would be easy to write a hive
   read/write pair that "just so happens" to land in the same namespace and
   call that "membership-scoped sharing" without ever exercising
   ``get_agent_groups``.  Mutation control (m2) below flips exactly this
   read to prove the (b) tests are sensitive to it.

Case ids: ``a-visible``, ``a-invisible``, ``b-member``, ``b-nonmember``,
``c-filter-in-project``, ``c-filter-cross-project``, ``guard-project``,
``guard-agent``.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from starlette.testclient import TestClient

import tapps_brain.http_adapter as _http_mod
from tapps_brain.http_adapter import _service_version, _Settings, create_app
from tapps_brain.mcp_server import create_server
from tests._pg_fixture import resolve_fixture_dsn

# NOTE: deliberately no `requires_postgres` mark (see tests/test_tenancy_migration.py's
# identical note). conftest.py's requires_postgres auto-skip checks
# TAPPS_BRAIN_DATABASE_URL in os.environ at collection time, before any fixture
# runs — it would silently skip this whole file rather than let the `dsn` fixture
# below start the disposable container. This module calls resolve_fixture_dsn()
# directly, which fails loudly (never skips) when neither a DSN nor docker is
# available, per lane policy for tenancy coverage.

# ---------------------------------------------------------------------------
# Fixture identifiers — unique per collection so repeated runs against a
# reused fixture DSN (CI's compose Postgres) never collide.
# ---------------------------------------------------------------------------

_SUFFIX = uuid.uuid4().hex[:8]
PROJECT_A = f"tenmatrix-a-{_SUFFIX}"
PROJECT_B = f"tenmatrix-b-{_SUFFIX}"
GROUP_G = f"tenmatrix-g-{_SUFFIX}"
GROUP_H = f"tenmatrix-h-{_SUFFIX}"
AGENT_X = "tenmatrix-x"
AGENT_Y = "tenmatrix-y"
AGENT_Z = "tenmatrix-z"


# ---------------------------------------------------------------------------
# Fixtures — Postgres + registered/approved projects + Hive membership
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _allow_privileged_role() -> Any:
    """The fixture DSN connects as the container's ``postgres`` superuser.

    ``PostgresConnectionManager`` refuses to pool a BYPASSRLS/superuser role
    (``_assert_non_privileged_role``) unless ``TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1``
    is set — the same override CI's compose fixture sets
    (``tests/test_maintenance_cycle.py``). Module-scoped (not the function-scoped
    ``monkeypatch`` fixture) so it is set before the module-scoped ``dsn`` /
    ``_seed_registry_and_membership`` fixtures below make their first connection.
    """
    prev = os.environ.get("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE")
    os.environ["TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE"] = "1"
    yield
    if prev is None:
        os.environ.pop("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE", None)
    else:
        os.environ["TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE"] = prev


@pytest.fixture(scope="module")
def dsn(_allow_privileged_role: Any) -> str:
    """An ephemeral, migrated Postgres DSN (never the deployed brain)."""
    d = resolve_fixture_dsn()
    from tapps_brain.postgres_migrations import apply_hive_migrations, apply_private_migrations

    apply_private_migrations(d)
    apply_hive_migrations(d)
    return d


@pytest.fixture(scope="module", autouse=True)
def _seed_registry_and_membership(dsn: str):
    """Register proj-a/proj-b (approved) and Hive group ``g`` membership.

    ``x`` joins ``g`` under ``proj-a``; ``y`` joins ``g`` under ``proj-b``;
    ``z`` is deliberately never added — it is the case (b) non-member control.
    """
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_hive import PostgresHiveBackend
    from tapps_brain.profile import get_builtin_profile
    from tapps_brain.project_registry import ProjectRegistry

    cm = PostgresConnectionManager(dsn)
    registry = ProjectRegistry(cm)
    profile = get_builtin_profile("repo-brain")
    registry.register(PROJECT_A, profile, source="admin", approved=True)
    registry.register(PROJECT_B, profile, source="admin", approved=True)

    hive = PostgresHiveBackend(cm)
    hive.create_group(GROUP_G)
    added_x = hive.add_group_member(GROUP_G, AGENT_X, PROJECT_A)
    added_y = hive.add_group_member(GROUP_G, AGENT_Y, PROJECT_B)
    assert added_x is True
    assert added_y is True

    yield

    with cm.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM private_memories WHERE project_id = ANY(%s)",
            ([PROJECT_A, PROJECT_B],),
        )
        cur.execute("DELETE FROM hive_memories WHERE namespace = %s", (GROUP_G,))
        cur.execute("DELETE FROM hive_group_members WHERE group_name = %s", (GROUP_G,))
        cur.execute("DELETE FROM hive_groups WHERE name = %s", (GROUP_G,))
        cur.execute(
            "DELETE FROM project_profiles WHERE project_id = ANY(%s)",
            ([PROJECT_A, PROJECT_B],),
        )
        conn.commit()
    cm.close()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, dsn: str) -> None:
    monkeypatch.setenv("TAPPS_BRAIN_DATABASE_URL", dsn)
    monkeypatch.setenv("TAPPS_BRAIN_HIVE_DSN", dsn)
    monkeypatch.setenv("TAPPS_BRAIN_STRICT_PROJECTS", "1")
    monkeypatch.setenv("TAPPS_BRAIN_STRICT_AGENT_ID", "1")
    monkeypatch.setenv("TAPPS_BRAIN_STATELESS_HTTP", "1")
    # Starlette's TestClient sends Host: testserver; the mcp SDK's DNS-rebinding
    # guard (mcp_server/server.py::_build_transport_security) rejects any host
    # not on this allow-list once one is configured.
    monkeypatch.setenv("TAPPS_BRAIN_MCP_ALLOWED_HOSTS", "testserver")


@pytest.fixture(autouse=True)
def _clear_probe_cache() -> Any:
    import tapps_brain.http.probe_cache as _pc

    _pc._PROBE_CACHE.clear()
    yield
    _pc._PROBE_CACHE.clear()


# ---------------------------------------------------------------------------
# App builders — mirrors tests/test_http_tenant_refusal.py's local helpers
# (kept self-contained per that module's own file-partition note) and
# tests/test_http_mcp_parity.py's in-process ASGI pattern.
# ---------------------------------------------------------------------------


def _make_settings(*, store: Any) -> _Settings:
    s = _Settings.__new__(_Settings)
    s.dsn = None
    s.auth_token = None
    s.admin_token = None
    s.metrics_token = None
    s.allowed_origins = []
    s.version = _service_version()
    s.store = store
    s.snapshot_lock = threading.Lock()
    s.snapshot_cache = None
    s.snapshot_cache_at = 0.0
    return s


@contextmanager
def _rest_client():
    """REST-only app: ``cfg.store`` is a mock never dereferenced.

    Every ``/v1/*`` request in this file supplies an explicit
    ``X-Project-Id`` naming a registered project, so ``_get_store_for_project``
    always takes the real-store factory path (see
    ``mcp_server/context.py::_get_store_for_project``) and the mock default
    store is never touched.
    """
    settings = _make_settings(store=MagicMock())
    with (
        patch.object(_http_mod, "_settings", settings),
        patch.object(_http_mod, "get_settings", return_value=settings),
    ):
        mcp_dummy = MagicMock()
        mcp_dummy.session_manager = None
        app = create_app(mcp_server=mcp_dummy)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


def _build_mcp_app(agent_id: str) -> Any:
    """A FastAPI app with a real FastMCP server bound to *agent_id*.

    ``agent_id`` is the tool-registration-time default (``ToolContext.
    server_agent_id``); ``hive_search`` always acts as this identity (it takes
    no per-call ``agent_id`` argument), so case (b)'s three actors each need
    their own app instance. ``memory_save`` / ``memory_search`` *do* accept a
    per-call ``agent_id`` override and are driven from one shared app for (a)
    writes and (c).
    """
    mcp = create_server(Path.cwd(), enable_hive=True, agent_id=agent_id)
    return create_app(mcp_server=mcp)


def _call_tool(
    client: TestClient, tool: str, arguments: dict[str, Any], *, project: str, agent: str
) -> Any:
    """POST one ``tools/call`` and return the tool's parsed JSON payload.

    ``agent`` is mandatory (not defaulted) and always sent as ``X-Agent-Id``.
    Without it, ``_StoreProxy._resolve()``'s per-request agent resolution
    (``mcp_server/context.py``) reads the transport's default identity, which
    is the literal string ``"unknown"`` — a truthy value that then WINS over
    the server's own bound ``agent_id`` in ``effective_agent_id = call_agent_id
    or agent_id`` (headerless requests are supposed to mean "no override", but
    the sentinel string does not read as falsy). ``store._hive_agent_id`` then
    resolves to ``"unknown"`` instead of the real caller, so ``hive_search``'s
    ``get_agent_groups("unknown", project_id)`` returns no memberships and (b)
    would fail for a reason unrelated to the isolation being tested here.
    Naming the caller explicitly on every call sidesteps that ambiguity the
    same way the REST helpers below already do via ``X-Agent-Id``.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": f"call-{tool}-{uuid.uuid4().hex[:6]}",
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-Project-Id": project,
        "X-Agent-Id": agent,
    }
    # ``_build_mcp_app`` builds a real app via ``create_app`` without patching
    # ``get_settings`` (unlike ``_rest_client``), so it resolves a real
    # ``_Settings`` from the ambient environment — including whatever
    # ``TAPPS_BRAIN_AUTH_TOKEN`` this dev deployment already has set. Mirror
    # ``tests/test_http_mcp_parity.py``'s bearer-token handling rather than
    # patch it away, since the point of these tests is to run through the
    # real auth path, not stub around it.
    auth_token = os.environ.get("TAPPS_BRAIN_AUTH_TOKEN", "")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    resp = client.post("/mcp", json=payload, headers=headers)
    assert resp.status_code < 400, f"{tool}: transport error {resp.status_code} {resp.text[:500]}"
    ctype = resp.headers.get("content-type", "")
    if "application/json" in ctype:
        body = resp.json()
    elif "text/event-stream" in ctype:
        text = resp.text
        data_line = next(
            (ln[len("data:") :].strip() for ln in text.splitlines() if ln.startswith("data:")),
            "",
        )
        assert data_line, f"{tool}: empty SSE body"
        body = json.loads(data_line)
    else:
        pytest.fail(f"{tool}: unexpected content-type {ctype!r}")
    assert "result" in body, f"{tool}: no result in {body}"
    content = body["result"]["content"]
    return json.loads(content[0]["text"])


def _remember(client: TestClient, *, project: str, agent: str, key: str, value: str) -> dict:
    resp = client.post(
        "/v1/remember",
        json={"key": key, "value": value, "tier": "context"},
        headers={"X-Project-Id": project, "X-Agent-Id": agent},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _recall(client: TestClient, *, project: str, agent: str, query: str) -> list[dict]:
    resp = client.post(
        "/v1/recall",
        json={"query": query, "max_results": 10},
        headers={"X-Project-Id": project, "X-Agent-Id": agent},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


# ---------------------------------------------------------------------------
# (a) Private isolation — REST /v1/remember + /v1/recall
# ---------------------------------------------------------------------------


def test_a_visible(dsn: str) -> None:
    key = f"a-visible-{uuid.uuid4().hex[:8]}"
    with _rest_client() as client:
        _remember(
            client, project=PROJECT_A, agent=AGENT_X, key=key, value=f"tenmatrix payload {key}"
        )
        results = _recall(client, project=PROJECT_A, agent=AGENT_X, query=key)
    assert key in {r["key"] for r in results}, results


def test_a_invisible(dsn: str) -> None:
    key = f"a-invisible-{uuid.uuid4().hex[:8]}"
    with _rest_client() as client:
        _remember(
            client, project=PROJECT_A, agent=AGENT_X, key=key, value=f"tenmatrix payload {key}"
        )
        results = _recall(client, project=PROJECT_B, agent=AGENT_X, query=key)
    assert key not in {r["key"] for r in results}, results


# ---------------------------------------------------------------------------
# (b) Group-scoped Hive sharing — MCP memory_save (private) + MCP
# hive_propagate(agent_scope=group:<g>) + MCP hive_search, membership
# resolved live from hive_group_members on both the write and read side.
# ---------------------------------------------------------------------------


def _save_and_propagate_to_group(client: TestClient, *, key: str, project: str, agent: str) -> None:
    """Save privately, then explicitly propagate to Hive under ``GROUP_G``.

    Deliberately two calls, not one ``memory_save(agent_scope="group:<g>")``:
    that single-call path additionally gates on ``MemoryStore._groups``
    (``store.py:1784-1788`` — ``validate_scope_and_group``), a *static*
    membership list populated only from ``profile.hive.groups`` at store
    construction (``store.py:552-554``). That field is a **project-wide**
    declaration — every agent whose store is built under that project's
    profile auto-joins it (``store.py:_setup_group_memberships``), so setting
    it to make ``x`` pass the gate would silently also enroll ``z`` (proj-a's
    other agent) as a member, defeating the (b) non-member case. The
    ``hive_propagate`` MCP tool has no such gate: it calls
    ``PropagationEngine.propagate`` directly, whose only membership check is
    ``hive_store.agent_is_group_member(group_ns, agent_id)`` — a live
    ``hive_group_members`` read (``backends.py:109``) — so only an agent
    actually added via ``hive.add_group_member`` in the seeding fixture can
    propagate into the group namespace. ``force=True`` bypasses the
    ``repo-brain`` profile's ``private_tiers=["context"]`` rule (which would
    otherwise downgrade the propagation to "private" for a context-tier
    entry) without touching the membership check.
    """
    saved = _call_tool(
        client,
        "memory_save",
        {"key": key, "value": f"tenmatrix cross project group payload {key}", "tier": "context"},
        project=project,
        agent=agent,
    )
    assert saved.get("status") == "saved", saved

    propagated = _call_tool(
        client,
        "hive_propagate",
        {"key": key, "agent_scope": f"group:{GROUP_G}", "force": True},
        project=project,
        agent=agent,
    )
    assert propagated.get("propagated") is True, propagated
    assert propagated.get("namespace") == GROUP_G, propagated


def test_b_member(dsn: str) -> None:
    key = f"b-shared-{uuid.uuid4().hex[:8]}"
    writer_app = _build_mcp_app(AGENT_X)
    with TestClient(writer_app, raise_server_exceptions=False) as client:
        _save_and_propagate_to_group(client, key=key, project=PROJECT_A, agent=AGENT_X)

    reader_app = _build_mcp_app(AGENT_Y)
    with TestClient(reader_app, raise_server_exceptions=False) as client:
        found = _call_tool(client, "hive_search", {"query": key}, project=PROJECT_B, agent=AGENT_Y)
    assert "error" not in found, found
    assert key in {r.get("key") for r in found.get("results", [])}, found


def test_b_nonmember(dsn: str) -> None:
    key = f"b-shared-{uuid.uuid4().hex[:8]}"
    writer_app = _build_mcp_app(AGENT_X)
    with TestClient(writer_app, raise_server_exceptions=False) as client:
        _save_and_propagate_to_group(client, key=key, project=PROJECT_A, agent=AGENT_X)

    # Positive control first (defect-report shape): z can recall its own row,
    # so its later miss on the shared row is a real miss, not a broken client.
    own_key = f"b-outsider-own-{uuid.uuid4().hex[:8]}"
    outsider_app = _build_mcp_app(AGENT_Z)
    with TestClient(outsider_app, raise_server_exceptions=False) as client:
        own_saved = _call_tool(
            client,
            "memory_save",
            {
                "key": own_key,
                "value": f"tenmatrix outsider own payload {own_key}",
                "tier": "context",
            },
            project=PROJECT_A,
            agent=AGENT_Z,
        )
        assert own_saved.get("status") == "saved", own_saved
        own_found = _call_tool(
            client, "memory_search", {"query": own_key}, project=PROJECT_A, agent=AGENT_Z
        )
        assert own_key in {r.get("key") for r in own_found}, own_found

        found = _call_tool(client, "hive_search", {"query": key}, project=PROJECT_A, agent=AGENT_Z)
    assert "error" not in found, found
    assert key not in {r.get("key") for r in found.get("results", [])}, found


# ---------------------------------------------------------------------------
# (c) memory_group label inside one project — MCP memory_search(group=...)
# ---------------------------------------------------------------------------


def test_c_filter_in_project(dsn: str) -> None:
    key_g = f"c-g-{uuid.uuid4().hex[:8]}"
    key_h = f"c-h-{uuid.uuid4().hex[:8]}"
    app = _build_mcp_app(AGENT_X)
    with TestClient(app, raise_server_exceptions=False) as client:
        saved_g = _call_tool(
            client,
            "memory_save",
            {
                "key": key_g,
                "value": f"tenmatrix group label payload {key_g}",
                "tier": "context",
                "group": GROUP_G,
            },
            project=PROJECT_A,
            agent=AGENT_X,
        )
        saved_h = _call_tool(
            client,
            "memory_save",
            {
                "key": key_h,
                "value": f"tenmatrix group label payload {key_h}",
                "tier": "context",
                "group": GROUP_H,
            },
            project=PROJECT_A,
            agent=AGENT_X,
        )
        assert saved_g.get("status") == "saved", saved_g
        assert saved_h.get("status") == "saved", saved_h

        found = _call_tool(
            client,
            "memory_search",
            {"query": "tenmatrix group label payload", "group": GROUP_G},
            project=PROJECT_A,
            agent=AGENT_X,
        )
    keys = {r["key"] for r in found}
    assert key_g in keys, found
    assert key_h not in keys, found


def test_c_filter_cross_project(dsn: str) -> None:
    key_g = f"c-g-{uuid.uuid4().hex[:8]}"
    app = _build_mcp_app(AGENT_X)
    with TestClient(app, raise_server_exceptions=False) as client:
        saved_g = _call_tool(
            client,
            "memory_save",
            {
                "key": key_g,
                "value": f"tenmatrix group label payload {key_g}",
                "tier": "context",
                "group": GROUP_G,
            },
            project=PROJECT_A,
            agent=AGENT_X,
        )
        assert saved_g.get("status") == "saved", saved_g

        # Positive control: the same filter finds the row in its own project.
        own_project_hit = _call_tool(
            client,
            "memory_search",
            {"query": key_g, "group": GROUP_G},
            project=PROJECT_A,
            agent=AGENT_X,
        )
        assert key_g in {r["key"] for r in own_project_hit}, own_project_hit

        cross_project_hit = _call_tool(
            client,
            "memory_search",
            {"query": key_g, "group": GROUP_G},
            project=PROJECT_B,
            agent=AGENT_X,
        )
    assert key_g not in {r["key"] for r in cross_project_hit}, cross_project_hit


# ---------------------------------------------------------------------------
# Guards — the matrix must be running under the strict-tenancy flags (L2,
# TAP-7243), or a green matrix could come from a lax server.
# ---------------------------------------------------------------------------


def test_guard_project() -> None:
    with _rest_client() as client:
        resp = client.post("/v1/remember", json={"key": "k", "value": "v"})
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["code"] == "tenant_project_missing"
    assert body["gate"] == "tenant_scope"


def test_guard_agent() -> None:
    with _rest_client() as client:
        resp = client.post(
            "/v1/remember",
            json={"key": "k", "value": "v"},
            headers={"X-Project-Id": PROJECT_A, "X-Agent-Id": "unknown"},
        )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["code"] == "tenant_agent_literal"
    assert body["gate"] == "tenant_scope"
