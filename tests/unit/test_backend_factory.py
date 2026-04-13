"""Tests for backend factory functions (ADR-007 — Postgres-only Hive/Federation/Private)."""

from __future__ import annotations

import pytest

from tapps_brain._protocols import (
    AgentRegistryBackend,
    FederationBackend,
    HiveBackend,
    PrivateBackend,
)
from tapps_brain.backends import (
    FileAgentRegistryBackend,
    create_agent_registry_backend,
    create_federation_backend,
    create_hive_backend,
    create_private_backend,
    derive_project_id,
    resolve_hive_backend_from_env,
    resolve_private_backend_from_env,
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
        monkeypatch.delenv("TAPPS_BRAIN_DATABASE_URL", raising=False)
        assert resolve_hive_backend_from_env() is None

    def test_returns_none_when_env_empty(self, monkeypatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_HIVE_DSN", "")
        monkeypatch.delenv("TAPPS_BRAIN_DATABASE_URL", raising=False)
        assert resolve_hive_backend_from_env() is None

    def test_returns_none_when_env_whitespace(self, monkeypatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_HIVE_DSN", "   ")
        monkeypatch.delenv("TAPPS_BRAIN_DATABASE_URL", raising=False)
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
        assert isinstance(backend, FileAgentRegistryBackend)

    def test_none_returns_yaml_file_backend(self) -> None:
        backend = create_agent_registry_backend(None)
        assert isinstance(backend, FileAgentRegistryBackend)

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


class TestCreatePrivateBackend:
    """Factory tests for create_private_backend (EPIC-059 STORY-059.5)."""

    def test_requires_postgres_dsn(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_private_backend("", project_id="p", agent_id="a")

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_private_backend("   ", project_id="p", agent_id="a")

    def test_rejects_file_path(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_private_backend(str(tmp_path / "memory.db"), project_id="p", agent_id="a")

    def test_rejects_mysql_dsn(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_private_backend("mysql://localhost/brain", project_id="p", agent_id="a")

    def test_rejects_sqlite_uri(self) -> None:
        with pytest.raises(ValueError, match="ADR-007"):
            create_private_backend("sqlite:///memory.db", project_id="p", agent_id="a")

    def test_postgres_returns_postgres_backend(self) -> None:
        from tapps_brain.postgres_private import PostgresPrivateBackend

        backend = create_private_backend("postgres://localhost/brain", project_id="p", agent_id="a")
        try:
            assert isinstance(backend, PostgresPrivateBackend)
            assert isinstance(backend, PrivateBackend)
        finally:
            backend.close()

    def test_postgresql_prefix_accepted(self) -> None:
        from tapps_brain.postgres_private import PostgresPrivateBackend

        backend = create_private_backend(
            "postgresql://localhost/brain", project_id="p", agent_id="a"
        )
        try:
            assert isinstance(backend, PostgresPrivateBackend)
        finally:
            backend.close()


class TestDeriveProjectId:
    """Tests for the derive_project_id helper."""

    def test_returns_16_hex_chars(self, tmp_path) -> None:
        pid = derive_project_id(tmp_path)
        assert len(pid) == 16
        assert all(c in "0123456789abcdef" for c in pid)

    def test_stable_across_calls(self, tmp_path) -> None:
        assert derive_project_id(tmp_path) == derive_project_id(tmp_path)

    def test_different_paths_produce_different_ids(self, tmp_path) -> None:
        sub = tmp_path / "subdir"
        sub.mkdir()
        assert derive_project_id(tmp_path) != derive_project_id(sub)

    def test_accepts_string_path(self, tmp_path) -> None:
        assert derive_project_id(str(tmp_path)) == derive_project_id(tmp_path)


class TestResolvePrivateBackendFromEnv:
    """Tests for resolve_private_backend_from_env."""

    def test_returns_none_when_no_env(self, monkeypatch) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_DATABASE_URL", raising=False)
        monkeypatch.delenv("TAPPS_BRAIN_HIVE_DSN", raising=False)
        assert resolve_private_backend_from_env("pid", "aid") is None

    def test_uses_database_url(self, monkeypatch) -> None:
        from tapps_brain.postgres_private import PostgresPrivateBackend

        monkeypatch.setenv("TAPPS_BRAIN_DATABASE_URL", "postgres://localhost/brain")
        monkeypatch.delenv("TAPPS_BRAIN_HIVE_DSN", raising=False)
        backend = resolve_private_backend_from_env("pid", "aid")
        try:
            assert isinstance(backend, PostgresPrivateBackend)
        finally:
            if backend:
                backend.close()

    def test_falls_back_to_hive_dsn(self, monkeypatch) -> None:
        from tapps_brain.postgres_private import PostgresPrivateBackend

        monkeypatch.delenv("TAPPS_BRAIN_DATABASE_URL", raising=False)
        monkeypatch.setenv("TAPPS_BRAIN_HIVE_DSN", "postgres://localhost/brain")
        backend = resolve_private_backend_from_env("pid", "aid")
        try:
            assert isinstance(backend, PostgresPrivateBackend)
        finally:
            if backend:
                backend.close()

    def test_returns_none_for_invalid_dsn(self, monkeypatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_DATABASE_URL", "mysql://bad-dsn")
        monkeypatch.delenv("TAPPS_BRAIN_HIVE_DSN", raising=False)
        result = resolve_private_backend_from_env("pid", "aid")
        assert result is None
