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
)


class TestCreateHiveBackend:
    def test_requires_postgres_dsn(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_hive_backend(None)

    def test_rejects_non_postgres_path(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_hive_backend(str(tmp_path / "hive.db"))

    def test_postgres_returns_postgres_backend(self) -> None:
        from tapps_brain.postgres_hive import PostgresHiveBackend

        backend = create_hive_backend("postgres://localhost/brain")
        try:
            assert isinstance(backend, PostgresHiveBackend)
            assert isinstance(backend, HiveBackend)
        finally:
            backend.close()


class TestCreateFederationBackend:
    def test_requires_postgres_dsn(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_federation_backend(None)

    def test_rejects_file_path(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_federation_backend(str(tmp_path / "federated.db"))

    def test_postgres_returns_postgres_backend(self) -> None:
        from tapps_brain.postgres_federation import PostgresFederationBackend

        backend = create_federation_backend("postgres://localhost/brain")
        try:
            assert isinstance(backend, PostgresFederationBackend)
            assert isinstance(backend, FederationBackend)
        finally:
            backend.close()


class TestCreateAgentRegistryBackend:
    def test_default_returns_yaml_file_backend(self, tmp_path) -> None:
        path = tmp_path / "agents.yaml"
        backend = create_agent_registry_backend(str(path))
        assert isinstance(backend, SqliteAgentRegistryBackend)

    def test_postgres_returns_postgres_backend(self) -> None:
        from tapps_brain.postgres_hive import PostgresAgentRegistry

        backend = create_agent_registry_backend("postgres://localhost/brain")
        assert isinstance(backend, PostgresAgentRegistry)

    def test_satisfies_protocol(self, tmp_path) -> None:
        path = tmp_path / "agents.yaml"
        backend = create_agent_registry_backend(str(path))
        assert isinstance(backend, AgentRegistryBackend)
