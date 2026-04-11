"""Tests for backend factory functions (ADR-007 — Postgres-only Hive/Federation)."""

from __future__ import annotations

import pytest

from tapps_brain._protocols import (
    AgentRegistryBackend,
    FederationBackend,
    HiveBackend,
)
from tapps_brain.backends import (
    SqliteAgentRegistryBackend,
    create_agent_registry_backend,
    create_federation_backend,
    create_hive_backend,
    resolve_hive_backend_from_env,
)


class TestCreateHiveBackend:
    def test_requires_postgres_dsn(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_hive_backend(None)

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_hive_backend("")

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_hive_backend("   ")

    def test_rejects_non_postgres_path(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_hive_backend(str(tmp_path / "hive.db"))

    def test_rejects_mysql_dsn(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_hive_backend("mysql://localhost/brain")

    def test_rejects_sqlite_uri(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_hive_backend("sqlite:///memory.db")

    def test_postgres_returns_postgres_backend(self) -> None:
        from tapps_brain.postgres_hive import PostgresHiveBackend

        backend = create_hive_backend("postgres://localhost/brain")
        try:
            assert isinstance(backend, PostgresHiveBackend)
            assert isinstance(backend, HiveBackend)
        finally:
            backend.close()

    def test_postgresql_prefix_accepted(self) -> None:
        """The alternate 'postgresql://' prefix must also be accepted."""
        from tapps_brain.postgres_hive import PostgresHiveBackend

        backend = create_hive_backend("postgresql://localhost/brain")
        try:
            assert isinstance(backend, PostgresHiveBackend)
        finally:
            backend.close()

    def test_encryption_key_accepted_but_ignored(self) -> None:
        """encryption_key param kept for call-site compat; must not raise."""
        from tapps_brain.postgres_hive import PostgresHiveBackend

        backend = create_hive_backend("postgres://localhost/brain", encryption_key="secret")
        try:
            assert isinstance(backend, PostgresHiveBackend)
        finally:
            backend.close()


class TestCreateFederationBackend:
    def test_requires_postgres_dsn(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_federation_backend(None)

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_federation_backend("")

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_federation_backend("   ")

    def test_rejects_file_path(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_federation_backend(str(tmp_path / "federated.db"))

    def test_rejects_mysql_dsn(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_federation_backend("mysql://localhost/brain")

    def test_rejects_sqlite_uri(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_federation_backend("sqlite:///federated.db")

    def test_postgres_returns_postgres_backend(self) -> None:
        from tapps_brain.postgres_federation import PostgresFederationBackend

        backend = create_federation_backend("postgres://localhost/brain")
        try:
            assert isinstance(backend, PostgresFederationBackend)
            assert isinstance(backend, FederationBackend)
        finally:
            backend.close()

    def test_postgresql_prefix_accepted(self) -> None:
        """The alternate 'postgresql://' prefix must also be accepted."""
        from tapps_brain.postgres_federation import PostgresFederationBackend

        backend = create_federation_backend("postgresql://localhost/brain")
        try:
            assert isinstance(backend, PostgresFederationBackend)
        finally:
            backend.close()


class TestResolveHiveBackendFromEnv:
    def test_returns_none_when_env_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_HIVE_DSN", raising=False)
        assert resolve_hive_backend_from_env() is None

    def test_returns_none_when_env_empty(self, monkeypatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_HIVE_DSN", "")
        assert resolve_hive_backend_from_env() is None

    def test_returns_none_when_env_whitespace(self, monkeypatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_HIVE_DSN", "   ")
        assert resolve_hive_backend_from_env() is None

    def test_returns_postgres_backend_when_dsn_set(self, monkeypatch) -> None:
        from tapps_brain.postgres_hive import PostgresHiveBackend

        monkeypatch.setenv("TAPPS_BRAIN_HIVE_DSN", "postgres://localhost/brain")
        backend = resolve_hive_backend_from_env()
        try:
            assert isinstance(backend, PostgresHiveBackend)
        finally:
            if backend is not None:
                backend.close()

    def test_raises_on_invalid_dsn_in_env(self, monkeypatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_HIVE_DSN", "mysql://localhost/brain")
        with pytest.raises(ValueError, match="ADR-007"):
            resolve_hive_backend_from_env()


class TestCreateAgentRegistryBackend:
    def test_default_returns_yaml_file_backend(self, tmp_path) -> None:
        path = tmp_path / "agents.yaml"
        backend = create_agent_registry_backend(str(path))
        assert isinstance(backend, SqliteAgentRegistryBackend)

    def test_none_returns_yaml_file_backend(self) -> None:
        backend = create_agent_registry_backend(None)
        assert isinstance(backend, SqliteAgentRegistryBackend)

    def test_postgres_returns_postgres_backend(self) -> None:
        from tapps_brain.postgres_hive import PostgresAgentRegistry

        backend = create_agent_registry_backend("postgres://localhost/brain")
        assert isinstance(backend, PostgresAgentRegistry)

    def test_postgresql_prefix_returns_postgres_backend(self) -> None:
        from tapps_brain.postgres_hive import PostgresAgentRegistry

        backend = create_agent_registry_backend("postgresql://localhost/brain")
        assert isinstance(backend, PostgresAgentRegistry)

    def test_satisfies_protocol(self, tmp_path) -> None:
        path = tmp_path / "agents.yaml"
        backend = create_agent_registry_backend(str(path))
        assert isinstance(backend, AgentRegistryBackend)
