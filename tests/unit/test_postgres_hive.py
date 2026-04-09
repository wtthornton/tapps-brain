"""Unit tests for PostgresHiveBackend and PostgresAgentRegistry (mocked DB).

EPIC-055 — verifies SQL generation and method delegation without a real PG instance.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_backend() -> tuple:
    """Create a PostgresHiveBackend with a mocked connection manager."""
    from tapps_brain.postgres_hive import PostgresHiveBackend

    mock_cm = MagicMock()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()

    # Wire up context managers.
    mock_cm.get_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.get_connection.return_value.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    backend = PostgresHiveBackend(mock_cm)
    return backend, mock_cm, mock_conn, mock_cursor


class TestPostgresHiveBackendInit:
    def test_constructor_stores_connection_manager(self) -> None:
        from tapps_brain.postgres_hive import PostgresHiveBackend

        mock_cm = MagicMock()
        backend = PostgresHiveBackend(mock_cm)
        assert backend._cm is mock_cm

    def test_db_path_is_sentinel(self) -> None:
        from pathlib import Path

        from tapps_brain.postgres_hive import PostgresHiveBackend

        mock_cm = MagicMock()
        backend = PostgresHiveBackend(mock_cm)
        assert backend._db_path == Path("/dev/null")


class TestPostgresHiveBackendSave:
    def test_save_new_entry_executes_insert(self) -> None:
        backend, _, _, mock_cursor = _make_backend()

        # No existing entry.
        mock_cursor.fetchone.return_value = None

        result = backend.save(
            key="test-key",
            value="test-value",
            namespace="universal",
            source_agent="agent-1",
        )

        assert result is not None
        assert result["key"] == "test-key"
        assert result["value"] == "test-value"
        assert result["namespace"] == "universal"
        assert result["source_agent"] == "agent-1"
        assert result["tags"] == []
        # Verify INSERT was called.
        calls = mock_cursor.execute.call_args_list
        assert len(calls) >= 2  # SELECT existing + INSERT + UPDATE notify
        # First call: check existing.
        assert "SELECT" in calls[0][0][0]
        # Second call: INSERT.
        assert "INSERT INTO hive_memories" in calls[1][0][0]

    def test_save_conflict_rejection_returns_none(self) -> None:
        backend, _, _, mock_cursor = _make_backend()

        # Existing entry with higher confidence.
        mock_cursor.fetchone.return_value = (
            "universal",
            "k",
            "v",
            "agent-2",
            "pattern",
            0.9,
            "agent",
            "[]",
            None,
            None,
            None,
            None,
            "supersede",
            None,
            "2025-01-01",
            "2025-01-01",
            None,
        )
        mock_cursor.description = [
            ("namespace",),
            ("key",),
            ("value",),
            ("source_agent",),
            ("tier",),
            ("confidence",),
            ("source",),
            ("tags",),
            ("valid_at",),
            ("invalid_at",),
            ("superseded_by",),
            ("memory_group",),
            ("conflict_policy",),
            ("embedding",),
            ("created_at",),
            ("updated_at",),
            ("search_vector",),
        ]

        result = backend.save(
            key="k",
            value="new-v",
            namespace="universal",
            confidence=0.5,
            conflict_policy="confidence_max",
        )
        assert result is None


class TestPostgresHiveBackendGet:
    def test_get_returns_dict_when_found(self) -> None:
        backend, _, _, mock_cursor = _make_backend()

        mock_cursor.fetchone.return_value = (
            "universal",
            "my-key",
            "my-value",
            "agent-1",
            "pattern",
            0.6,
            "agent",
            "[]",
            None,
            None,
            None,
            None,
            "supersede",
            None,
            "2025-01-01",
            "2025-01-01",
            None,
        )
        mock_cursor.description = [
            ("namespace",),
            ("key",),
            ("value",),
            ("source_agent",),
            ("tier",),
            ("confidence",),
            ("source",),
            ("tags",),
            ("valid_at",),
            ("invalid_at",),
            ("superseded_by",),
            ("memory_group",),
            ("conflict_policy",),
            ("embedding",),
            ("created_at",),
            ("updated_at",),
            ("search_vector",),
        ]

        result = backend.get("my-key", "universal")
        assert result is not None
        assert result["key"] == "my-key"
        assert result["tags"] == []

    def test_get_returns_none_when_not_found(self) -> None:
        backend, _, _, mock_cursor = _make_backend()
        mock_cursor.fetchone.return_value = None

        result = backend.get("nonexistent")
        assert result is None


class TestPostgresHiveBackendSearch:
    def test_search_uses_plainto_tsquery(self) -> None:
        backend, _, _, mock_cursor = _make_backend()
        mock_cursor.fetchall.return_value = []
        mock_cursor.description = []

        backend.search("test query")

        sql = mock_cursor.execute.call_args[0][0]
        assert "plainto_tsquery" in sql
        assert "ts_rank" in sql

    def test_search_with_namespaces_uses_any(self) -> None:
        backend, _, _, mock_cursor = _make_backend()
        mock_cursor.fetchall.return_value = []
        mock_cursor.description = []

        backend.search("test", namespaces=["ns1", "ns2"])

        sql = mock_cursor.execute.call_args[0][0]
        assert "ANY" in sql


class TestPostgresHiveBackendGroups:
    def test_create_group_executes_insert(self) -> None:
        backend, _, _, mock_cursor = _make_backend()

        result = backend.create_group("test-group", "A group")
        assert result["name"] == "test-group"
        assert result["description"] == "A group"

        sql = mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO hive_groups" in sql

    def test_add_group_member_returns_false_when_group_missing(self) -> None:
        backend, _, _, mock_cursor = _make_backend()
        mock_cursor.fetchone.return_value = None

        result = backend.add_group_member("no-group", "agent-1")
        assert result is False

    def test_list_groups_returns_list(self) -> None:
        backend, _, _, mock_cursor = _make_backend()
        mock_cursor.fetchall.return_value = [("g1", "desc", "2025-01-01")]
        mock_cursor.description = [("name",), ("description",), ("created_at",)]

        result = backend.list_groups()
        assert len(result) == 1
        assert result[0]["name"] == "g1"


class TestPostgresHiveBackendFeedback:
    def test_record_feedback_event_executes_insert(self) -> None:
        backend, _, _, mock_cursor = _make_backend()

        backend.record_feedback_event(
            event_id="evt-1",
            namespace="universal",
            entry_key="k1",
            event_type="positive",
            session_id="s1",
            utility_score=0.9,
            details={"note": "good"},
            timestamp="2025-01-01T00:00:00Z",
        )

        sql = mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO hive_feedback_events" in sql


class TestPostgresHiveBackendNotify:
    def test_get_write_notify_state_returns_dict(self) -> None:
        backend, _, _, mock_cursor = _make_backend()
        mock_cursor.fetchone.return_value = (42, "2025-01-01T00:00:00Z")

        state = backend.get_write_notify_state()
        assert state["revision"] == 42


class TestPostgresHiveBackendIntrospection:
    def test_list_namespaces(self) -> None:
        backend, _, _, mock_cursor = _make_backend()
        mock_cursor.fetchall.return_value = [("ns1",), ("ns2",)]

        result = backend.list_namespaces()
        assert result == ["ns1", "ns2"]

    def test_count_by_namespace(self) -> None:
        backend, _, _, mock_cursor = _make_backend()
        mock_cursor.fetchall.return_value = [("ns1", 5), ("ns2", 3)]

        result = backend.count_by_namespace()
        assert result == {"ns1": 5, "ns2": 3}

    def test_count_by_agent(self) -> None:
        backend, _, _, mock_cursor = _make_backend()
        mock_cursor.fetchall.return_value = [("agent-1", 10)]

        result = backend.count_by_agent()
        assert result == {"agent-1": 10}


class TestPostgresHiveBackendClose:
    def test_close_delegates_to_connection_manager(self) -> None:
        backend, mock_cm, _, _ = _make_backend()
        backend.close()
        mock_cm.close.assert_called_once()


class TestPostgresAgentRegistry:
    def _make_registry(self) -> tuple:
        from tapps_brain.postgres_hive import PostgresAgentRegistry

        mock_cm = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        mock_cm.get_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.get_connection.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        registry = PostgresAgentRegistry(mock_cm)
        return registry, mock_cursor

    def test_register_executes_upsert(self) -> None:
        registry, mock_cursor = self._make_registry()

        agent = MagicMock()
        agent.id = "agent-1"
        agent.name = "Agent One"
        agent.profile = "repo-brain"
        agent.skills = ["code-review"]
        agent.groups = []
        agent.project_root = "/tmp/project"

        registry.register(agent)

        sql = mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO agent_registry" in sql
        assert "ON CONFLICT" in sql

    def test_unregister_returns_bool(self) -> None:
        registry, mock_cursor = self._make_registry()
        mock_cursor.rowcount = 1

        result = registry.unregister("agent-1")
        assert result is True

    def test_get_returns_none_when_missing(self) -> None:
        registry, mock_cursor = self._make_registry()
        mock_cursor.fetchone.return_value = None

        result = registry.get("nonexistent")
        assert result is None

    def test_list_agents_returns_list(self) -> None:
        registry, mock_cursor = self._make_registry()
        mock_cursor.fetchall.return_value = [
            ("a1", "Agent 1", "repo-brain", "[]", None, "[]", "2025-01-01", "2025-01-01"),
        ]
        mock_cursor.description = [
            ("id",),
            ("name",),
            ("profile",),
            ("skills",),
            ("project_root",),
            ("groups",),
            ("registered_at",),
            ("last_seen_at",),
        ]

        result = registry.list_agents()
        assert len(result) == 1
        assert result[0]["id"] == "a1"
