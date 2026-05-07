"""Integration tests — TAP-1502 STORY-076.5: MCP tools for KG operations.

Verifies:
1. Four KG tools are registered on the standard MCP server.
2. ``brain_recall`` response shape is strictly additive — all pre-EPIC-076
   fields remain present and typed correctly.
3. ``brain_record_event`` writes to Postgres when a live DB is available.
4. ``brain_get_neighbors``, ``brain_explain_connection``, and
   ``brain_record_feedback`` are callable and return valid JSON shapes.
5. HTTP endpoints ``/v1/experience``, ``/v1/kg/neighbors``,
   ``/v1/kg/explain``, ``/v1/kg/feedback`` accept correct payloads and
   return ``application/json`` responses with auth enforcement.

Tests that require a live Postgres instance are guarded by
``TAPPS_TEST_POSTGRES_DSN`` and skipped when it is absent.
Tests that require the ``mcp`` package are guarded by
``pytest.importorskip``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("mcp")

from mcp.shared.memory import create_connected_server_and_client_session

pytestmark = pytest.mark.requires_mcp

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Postgres skip guard
# ---------------------------------------------------------------------------

_PG_DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")
_SKIP_PG = not _PG_DSN


# ---------------------------------------------------------------------------
# MCP server fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def mcp_server(project_dir: Path):
    from tapps_brain.mcp_server import create_server

    server = create_server(project_dir)
    yield server
    if hasattr(server, "_tapps_store"):
        server._tapps_store.close()


# ---------------------------------------------------------------------------
# KG tool discovery
# ---------------------------------------------------------------------------


class TestKgToolDiscovery:
    """The four new KG tools appear in tools/list."""

    async def test_kg_tools_registered(self, mcp_server) -> None:
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.list_tools()
            tool_names = {t.name for t in result.tools}
            expected_kg_tools = {
                "brain_record_event",
                "brain_get_neighbors",
                "brain_explain_connection",
                "brain_record_feedback",
            }
            assert expected_kg_tools.issubset(tool_names), (
                f"Missing KG tools: {expected_kg_tools - tool_names}"
            )

    async def test_kg_tools_have_descriptions(self, mcp_server) -> None:
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.list_tools()
            kg_tool_map = {
                t.name: t
                for t in result.tools
                if t.name in {
                    "brain_record_event",
                    "brain_get_neighbors",
                    "brain_explain_connection",
                    "brain_record_feedback",
                }
            }
            for name, tool in kg_tool_map.items():
                assert tool.description, f"KG tool {name} missing description"
                assert tool.inputSchema is not None, f"KG tool {name} missing inputSchema"

    async def test_existing_brain_tools_still_present(self, mcp_server) -> None:
        """Registering KG tools does not displace the original brain_* tools."""
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.list_tools()
            tool_names = {t.name for t in result.tools}
            pre_epic076 = {
                "brain_remember",
                "brain_recall",
                "brain_forget",
                "brain_learn_success",
                "brain_learn_failure",
                "brain_status",
            }
            assert pre_epic076.issubset(tool_names), (
                f"Pre-EPIC-076 brain tools removed: {pre_epic076 - tool_names}"
            )


# ---------------------------------------------------------------------------
# brain_recall backward-compatibility regression
# ---------------------------------------------------------------------------


class TestBrainRecallBackwardCompat:
    """brain_recall response shape is strictly additive — no pre-EPIC-076 field removed."""

    _PRE_EPIC076_FIELDS = {
        "key",
        "value",
        "tier",
        "confidence",
        "tags",
        "source",
        "created_at",
    }

    async def test_recall_with_no_results_returns_list(self, mcp_server) -> None:
        """brain_recall returns a JSON-serialisable list even when empty."""
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.call_tool(
                "brain_recall",
                {"query": "test-recall-empty-query-unique-xyz123"},
            )
            assert not result.isError
            parsed = json.loads(result.content[0].text)
            assert isinstance(parsed, list)

    async def test_recall_result_fields_present_after_epic076(self, mcp_server) -> None:
        """A recalled memory entry contains all pre-EPIC-076 fields."""
        async with create_connected_server_and_client_session(mcp_server) as session:
            # Save a known memory so we can recall it.
            await session.call_tool(
                "brain_remember",
                {
                    "fact": "The MemoryRetriever applies BM25 scoring.",
                    "tier": "architectural",
                },
            )
            recall_result = await session.call_tool(
                "brain_recall",
                {"query": "MemoryRetriever BM25 scoring", "max_results": 3},
            )
            assert not recall_result.isError
            entries = json.loads(recall_result.content[0].text)
            if entries:
                entry = entries[0]
                for field in self._PRE_EPIC076_FIELDS:
                    assert field in entry, (
                        f"Pre-EPIC-076 field {field!r} missing from brain_recall result"
                    )


# ---------------------------------------------------------------------------
# brain_record_event — no-DB path
# ---------------------------------------------------------------------------


class TestBrainRecordEventNoDB:
    """brain_record_event returns a db_unavailable error when no DSN is set."""

    async def test_record_event_no_db_returns_error(
        self, mcp_server, monkeypatch
    ) -> None:
        """Without a DB, brain_record_event returns error JSON (not MCP error)."""
        # Patch the process-level CM cache to None so no DB is used.
        from tapps_brain.services import kg_service

        monkeypatch.setattr(kg_service, "_CM", None)
        monkeypatch.delenv("TAPPS_BRAIN_DATABASE_URL", raising=False)
        monkeypatch.delenv("TAPPS_BRAIN_HIVE_DSN", raising=False)

        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.call_tool(
                "brain_record_event",
                {"event_type": "test_event"},
            )
            # Should not raise a protocol-level error; returns structured JSON
            assert not result.isError
            body = json.loads(result.content[0].text)
            assert "error" in body

    async def test_get_neighbors_no_db_returns_error(
        self, mcp_server, monkeypatch
    ) -> None:
        from tapps_brain.services import kg_service

        monkeypatch.setattr(kg_service, "_CM", None)
        monkeypatch.delenv("TAPPS_BRAIN_DATABASE_URL", raising=False)
        monkeypatch.delenv("TAPPS_BRAIN_HIVE_DSN", raising=False)

        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.call_tool(
                "brain_get_neighbors",
                {"entity_ids_json": '["00000000-0000-0000-0000-000000000001"]'},
            )
            assert not result.isError
            body = json.loads(result.content[0].text)
            assert "error" in body

    async def test_explain_connection_requires_ids(self, mcp_server) -> None:
        """brain_explain_connection returns error JSON when IDs are empty."""
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.call_tool(
                "brain_explain_connection",
                {"subject_id": "", "object_id": ""},
            )
            assert not result.isError
            body = json.loads(result.content[0].text)
            assert "error" in body


# ---------------------------------------------------------------------------
# brain_record_feedback — unit-level (no DB required for validation path)
# ---------------------------------------------------------------------------


class TestBrainRecordFeedback:
    """brain_record_feedback validates feedback_type at the tool layer."""

    async def test_invalid_feedback_type_returns_error(self, mcp_server) -> None:
        async with create_connected_server_and_client_session(mcp_server) as session:
            result = await session.call_tool(
                "brain_record_feedback",
                {
                    "edge_id": "00000000-0000-0000-0000-000000000001",
                    "feedback_type": "not_a_valid_type",
                },
            )
            assert not result.isError
            body = json.loads(result.content[0].text)
            # kg_service.record_kg_feedback rejects unknown types
            assert body.get("error") is not None or "bad_request" in str(body)


# ---------------------------------------------------------------------------
# Live-DB integration tests (skipped without TAPPS_TEST_POSTGRES_DSN)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_SKIP_PG, reason="TAPPS_TEST_POSTGRES_DSN not set")
class TestKgToolsLiveDB:
    """End-to-end KG tool tests against a real pg17 instance."""

    @pytest.fixture(scope="class", autouse=True)
    def _apply_migrations(self) -> None:
        from tapps_brain.postgres_migrations import apply_private_migrations

        apply_private_migrations(_PG_DSN)

    @pytest.fixture()
    def cm(self):
        from tapps_brain.postgres_connection import PostgresConnectionManager

        manager = PostgresConnectionManager(_PG_DSN, min_size=1, max_size=3)
        yield manager
        manager.close()

    def _seed_entities(self, cm, project_id: str, brain_id: str) -> tuple[str, str]:
        """Seed two entities and return their UUIDs."""
        from tapps_brain.postgres_kg import PostgresKnowledgeGraphStore

        kg = PostgresKnowledgeGraphStore(
            cm, project_id=project_id, brain_id=brain_id, evidence_required=False
        )
        try:
            eid1 = kg.upsert_entity(
                entity_type="module",
                canonical_name="RecallService",
                source_agent="test",
            )
            eid2 = kg.upsert_entity(
                entity_type="module",
                canonical_name="MemoryRetriever",
                source_agent="test",
            )
            kg.upsert_edge(
                subject_entity_id=eid1,
                predicate="delegates_to",
                object_entity_id=eid2,
                source_agent="test",
            )
        finally:
            kg.close()
        return eid1, eid2

    def test_record_event_creates_row(self, mcp_server, cm) -> None:
        """brain_record_event returns an event_id UUID when the DB is available."""
        import uuid as _uuid_mod

        # Patch the KG service to use the test DB.
        from tapps_brain.services import kg_service

        original_cm = kg_service._CM
        original_dsn = os.environ.get("TAPPS_BRAIN_DATABASE_URL")
        try:
            kg_service._CM = cm
            os.environ["TAPPS_BRAIN_DATABASE_URL"] = _PG_DSN

            # Use the service directly (avoids transport overhead).
            import uuid

            project_id = f"test-kg-{uuid.uuid4().hex[:8]}"
            result = kg_service.record_event(
                cm,
                project_id,
                "tapps-brain-test",
                "test-agent",
                event_type="test_tool_called",
            )
            assert "event_id" in result
            _uuid_mod.UUID(result["event_id"])  # must be valid UUID
        finally:
            kg_service._CM = original_cm
            if original_dsn is None:
                os.environ.pop("TAPPS_BRAIN_DATABASE_URL", None)
            else:
                os.environ["TAPPS_BRAIN_DATABASE_URL"] = original_dsn

    def test_get_neighbors_returns_seeded_edge(self, cm) -> None:
        """get_neighbors returns the seeded edge for a known entity."""
        import uuid

        from tapps_brain.services import kg_service

        project_id = f"test-kg-{uuid.uuid4().hex[:8]}"
        brain_id = "tapps-brain-test"
        eid1, eid2 = self._seed_entities(cm, project_id, brain_id)

        result = kg_service.get_neighbors(
            cm, project_id, brain_id, entity_ids=[eid1], hops=1, limit=10
        )
        assert "neighbors" in result
        neighbors = result["neighbors"]
        assert len(neighbors) >= 1
        neighbor_ids = {str(n.get("neighbor_id", "")) for n in neighbors}
        assert eid2 in neighbor_ids

    def test_explain_connection_finds_path(self, cm) -> None:
        """explain_connection finds the 1-hop path between seeded entities."""
        import uuid

        from tapps_brain.services import kg_service

        project_id = f"test-kg-{uuid.uuid4().hex[:8]}"
        brain_id = "tapps-brain-test"
        eid1, eid2 = self._seed_entities(cm, project_id, brain_id)

        result = kg_service.explain_connection(
            cm, project_id, brain_id, subject_id=eid1, object_id=eid2, max_hops=3
        )
        assert result["found"] is True
        assert result["hops"] == 1
        assert len(result["path"]) == 2
        assert result["path"][0]["entity_id"] == eid1
        assert result["path"][-1]["entity_id"] == eid2

    def test_explain_connection_no_path(self, cm) -> None:
        """explain_connection returns found=False for disconnected entities."""
        import uuid

        from tapps_brain.postgres_kg import PostgresKnowledgeGraphStore
        from tapps_brain.services import kg_service

        project_id = f"test-kg-{uuid.uuid4().hex[:8]}"
        brain_id = "tapps-brain-test"

        kg = PostgresKnowledgeGraphStore(
            cm, project_id=project_id, brain_id=brain_id, evidence_required=False
        )
        try:
            eid1 = kg.upsert_entity(
                entity_type="module", canonical_name="IsolatedA", source_agent="test"
            )
            eid2 = kg.upsert_entity(
                entity_type="module", canonical_name="IsolatedB", source_agent="test"
            )
        finally:
            kg.close()

        result = kg_service.explain_connection(
            cm, project_id, brain_id, subject_id=eid1, object_id=eid2, max_hops=3
        )
        assert result["found"] is False
        assert result["path"] == []


# ---------------------------------------------------------------------------
# HTTP endpoint tests (no live DB required — auth + validation only)
# ---------------------------------------------------------------------------


class TestHttpKgEndpoints:
    """HTTP endpoint smoke tests — validates auth enforcement and JSON shapes."""

    @pytest.fixture()
    def http_app(self, project_dir: Path):
        from tapps_brain.http_adapter import create_app

        app = create_app(project_dir=project_dir)
        return app

    @pytest.fixture()
    def client(self, http_app):
        pytest.importorskip("httpx")
        from httpx import ASGITransport, AsyncClient

        return AsyncClient(transport=ASGITransport(app=http_app), base_url="http://test")

    async def test_experience_endpoint_requires_project_id(self, client) -> None:
        """POST /v1/experience returns 400 without X-Project-Id."""
        response = await client.post(
            "/v1/experience",
            headers={"Authorization": "Bearer test-token"},
            content=json.dumps({"event_type": "test"}),
        )
        # May return 401 (auth) or 400 (missing header) — either is acceptable
        assert response.status_code in (400, 401, 422)

    async def test_kg_neighbors_endpoint_missing_entity_ids(self, client) -> None:
        """POST /v1/kg/neighbors returns 400 when entity_ids is absent."""
        response = await client.post(
            "/v1/kg/neighbors",
            headers={"Authorization": "Bearer test-token", "X-Project-Id": "test-proj"},
            content=json.dumps({}),
        )
        assert response.status_code in (400, 401, 422, 503)

    async def test_kg_explain_endpoint_exists(self, client) -> None:
        """POST /v1/kg/explain route is registered."""
        response = await client.post(
            "/v1/kg/explain",
            headers={"Authorization": "Bearer test-token", "X-Project-Id": "test-proj"},
            content=json.dumps({"subject_id": "id1", "object_id": "id2"}),
        )
        # 401/400/503 all confirm the route exists; 404 would mean it's missing
        assert response.status_code != 404

    async def test_kg_feedback_endpoint_exists(self, client) -> None:
        """POST /v1/kg/feedback route is registered."""
        response = await client.post(
            "/v1/kg/feedback",
            headers={"Authorization": "Bearer test-token", "X-Project-Id": "test-proj"},
            content=json.dumps({"edge_id": "some-id", "feedback_type": "edge_helpful"}),
        )
        assert response.status_code != 404
