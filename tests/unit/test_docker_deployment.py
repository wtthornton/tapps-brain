"""Tests for EPIC-058: Docker & Deployment Support.

All tests run without Docker or Postgres installed — they check file
existence, mock subprocess calls, and verify model fields.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

# Repo root — two levels up from this test file
_REPO_ROOT = Path(__file__).resolve().parents[2]


# ===================================================================
# STORY-058.1: Docker artefact existence
# ===================================================================


class TestDockerArtifacts:
    def test_docker_compose_exists(self):
        """docker-compose.hive.yaml exists, is valid YAML, and wires the 2026 unified stack."""
        path = _REPO_ROOT / "docker" / "docker-compose.hive.yaml"
        assert path.exists(), f"Missing {path}"
        data = yaml.safe_load(path.read_text())
        assert "services" in data
        # Unified stack service names (post-2026-04-21 rename):
        assert "tapps-brain-db" in data["services"]
        assert "tapps-brain-http" in data["services"]
        assert "tapps-brain-migrate" in data["services"]
        # Legacy names removed — regression guard.
        assert "tapps-hive-db" not in data["services"]
        assert "tapps-hive-migrate" not in data["services"]

    def test_init_db_sql_exists(self):
        """docker/init-db.sql (renamed from init-hive.sql) bootstraps the vector extension."""
        path = _REPO_ROOT / "docker" / "init-db.sql"
        assert path.exists(), f"Missing {path}"
        content = path.read_text()
        assert "CREATE EXTENSION" in content
        # Legacy filename must not linger.
        assert not (_REPO_ROOT / "docker" / "init-hive.sql").exists()

    def test_dockerfile_migrate_exists(self):
        path = _REPO_ROOT / "docker" / "Dockerfile.migrate"
        assert path.exists(), f"Missing {path}"
        content = path.read_text()
        assert "tapps-brain" in content
        # Image must ship the migrate entrypoint shell script so the sidecar
        # can apply schemas + create the tapps_runtime role in one step.
        assert "migrate-entrypoint.sh" in content

    def test_migrate_entrypoint_exists(self):
        """docker/migrate-entrypoint.sh drives the bootstrap (EPIC-058 + role split)."""
        path = _REPO_ROOT / "docker" / "migrate-entrypoint.sh"
        assert path.exists(), f"Missing {path}"
        assert os.access(path, os.X_OK), f"{path} must be executable"
        content = path.read_text()
        # The four-step bootstrap must cover schema + role + runtime password.
        assert "migrate-hive" in content
        assert "tapps_runtime" in content
        assert "TAPPS_BRAIN_RUNTIME_PASSWORD" in content

    def test_env_example_exists(self):
        path = _REPO_ROOT / "docker" / ".env.example"
        assert path.exists(), f"Missing {path}"
        content = path.read_text()
        # The .env template drives compose variable substitution — every
        # required var the compose file references with `:?` must be listed.
        assert "TAPPS_BRAIN_DB_PASSWORD" in content
        assert "TAPPS_BRAIN_RUNTIME_PASSWORD" in content
        assert "TAPPS_BRAIN_AUTH_TOKEN" in content
        assert "TAPPS_BRAIN_ADMIN_TOKEN" in content


# ===================================================================
# STORY-058.2: Auto-migration env var
# ===================================================================


class TestAutoMigration:
    def test_auto_migrate_env_var_true(self):
        """TAPPS_BRAIN_HIVE_AUTO_MIGRATE=true triggers migration."""
        env_val = "true"
        assert env_val.lower() in ("true", "1", "yes")

    def test_auto_migrate_env_var_false(self):
        """Empty or unset does not trigger migration."""
        for val in ("", "false", "no", "0"):
            assert val.lower() not in ("true", "1", "yes")

    def test_auto_migrate_env_var_yes(self):
        assert "yes" in ("true", "1", "yes")

    def test_auto_migrate_env_var_one(self):
        assert "1" in ("true", "1", "yes")


# ===================================================================
# STORY-058.3: Hive health fields on StoreHealthReport
# ===================================================================


class TestHealthReportHiveFields:
    def test_health_report_has_hive_fields(self):
        from tapps_brain.metrics import StoreHealthReport

        report = StoreHealthReport(store_path="/tmp/test")
        assert hasattr(report, "hive_connected")
        assert hasattr(report, "hive_schema_version")
        assert hasattr(report, "hive_schema_current")
        assert hasattr(report, "hive_pool_size")
        assert hasattr(report, "hive_pool_available")
        assert hasattr(report, "hive_latency_ms")

    def test_health_report_hive_defaults(self):
        from tapps_brain.metrics import StoreHealthReport

        report = StoreHealthReport(store_path="/tmp/test")
        assert report.hive_connected is False
        assert report.hive_schema_version == 0
        assert report.hive_schema_current is True
        assert report.hive_pool_size == 0
        assert report.hive_pool_available == 0
        assert report.hive_latency_ms == 0.0


# ===================================================================
# STORY-058.4: Backup / restore CLI commands
# ===================================================================

pytestmark_cli = pytest.mark.requires_cli


class TestBackupHiveCLI:
    @pytest.mark.requires_cli
    def test_backup_hive_requires_dsn(self):
        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        # Ensure env var is not set
        env = {k: v for k, v in os.environ.items() if k != "TAPPS_BRAIN_HIVE_DSN"}
        result = runner.invoke(app, ["maintenance", "backup-hive"], env=env)
        assert result.exit_code != 0
        assert "Error" in result.output or "dsn" in result.output.lower()

    @pytest.mark.requires_cli
    def test_restore_hive_requires_dsn(self):
        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        env = {k: v for k, v in os.environ.items() if k != "TAPPS_BRAIN_HIVE_DSN"}
        result = runner.invoke(app, ["maintenance", "restore-hive", "backup.sql"], env=env)
        assert result.exit_code != 0
        assert "Error" in result.output or "dsn" in result.output.lower()

    @pytest.mark.requires_cli
    def test_backup_hive_calls_pg_dump(self):
        """When DSN is provided, pg_dump is invoked."""
        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(
                app,
                [
                    "maintenance",
                    "backup-hive",
                    "--dsn",
                    "postgres://tapps:pass@localhost/tapps_brain",
                    "--output",
                    "/tmp/test-backup.sql",
                ],
            )
            assert result.exit_code == 0
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "pg_dump"

    @pytest.mark.requires_cli
    def test_restore_hive_calls_psql_for_sql(self):
        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(
                app,
                [
                    "maintenance",
                    "restore-hive",
                    "--dsn",
                    "postgres://tapps:pass@localhost/tapps_brain",
                    "backup.sql",
                ],
            )
            assert result.exit_code == 0
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "psql"


class TestStripDsnPassword:
    """Unit tests for the _strip_dsn_password helper (TAP-606)."""

    def test_url_format_extracts_password(self) -> None:
        from tapps_brain.cli import _strip_dsn_password

        safe, pw = _strip_dsn_password("postgres://user:s3cr3t@host:5432/dbname")
        assert pw == "s3cr3t"
        assert "s3cr3t" not in safe
        assert "user" in safe
        assert "host" in safe

    def test_url_format_no_password(self) -> None:
        from tapps_brain.cli import _strip_dsn_password

        dsn = "postgres://user@host/dbname"
        safe, pw = _strip_dsn_password(dsn)
        assert pw is None
        assert safe == dsn

    def test_kwv_format_extracts_password(self) -> None:
        from tapps_brain.cli import _strip_dsn_password

        safe, pw = _strip_dsn_password("host=localhost user=tapps password=s3cr3t dbname=db")
        assert pw == "s3cr3t"
        assert "s3cr3t" not in safe
        assert "host=localhost" in safe

    def test_kwv_quoted_password(self) -> None:
        from tapps_brain.cli import _strip_dsn_password

        safe, pw = _strip_dsn_password("host=localhost password='my secret' dbname=db")
        assert pw == "my secret"
        assert "my secret" not in safe

    def test_postgresql_scheme_alias(self) -> None:
        from tapps_brain.cli import _strip_dsn_password

        safe, pw = _strip_dsn_password("postgresql://u:pw123@host/db")
        assert pw == "pw123"
        assert "pw123" not in safe


class TestBackupHivePasswordSecurity:
    """Security tests: DSN password must not appear in pg_dump argv (TAP-606)."""

    SECRET = "supersecretpassword"
    DSN_WITH_SECRET = f"postgres://tapps:{SECRET}@localhost:5432/tapps_brain"

    @pytest.mark.requires_cli
    def test_backup_hive_password_not_in_argv(self) -> None:
        """pg_dump argv must not contain the DSN password."""
        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(
                app,
                [
                    "maintenance",
                    "backup-hive",
                    "--dsn",
                    self.DSN_WITH_SECRET,
                    "--output",
                    "/tmp/test-backup.sql",
                ],
            )
            assert result.exit_code == 0
            cmd = mock_run.call_args[0][0]
            # Password must NOT appear in any argv element
            assert all(self.SECRET not in arg for arg in cmd), f"Password leaked into argv: {cmd}"

    @pytest.mark.requires_cli
    def test_backup_hive_pgpassword_in_env(self) -> None:
        """subprocess.run must receive PGPASSWORD in its env kwarg."""
        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            runner.invoke(
                app,
                [
                    "maintenance",
                    "backup-hive",
                    "--dsn",
                    self.DSN_WITH_SECRET,
                    "--output",
                    "/tmp/test-backup.sql",
                ],
            )
            call_kwargs = mock_run.call_args[1]
            assert "env" in call_kwargs, "subprocess.run must receive env kwarg"
            assert call_kwargs["env"].get("PGPASSWORD") == self.SECRET

    @pytest.mark.requires_cli
    def test_backup_hive_error_stderr_scrubbed(self) -> None:
        """Password must be scrubbed from error output echoed to the user."""
        from subprocess import CalledProcessError

        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            err = CalledProcessError(
                1,
                ["pg_dump"],
                stderr=f"connection to server failed: password={self.SECRET}",
            )
            mock_run.side_effect = err
            result = runner.invoke(
                app,
                [
                    "maintenance",
                    "backup-hive",
                    "--dsn",
                    self.DSN_WITH_SECRET,
                    "--output",
                    "/tmp/test-backup.sql",
                ],
            )
            assert result.exit_code != 0
            assert self.SECRET not in result.output, "Password leaked into error output"

    @pytest.mark.requires_cli
    def test_restore_hive_password_not_in_argv(self) -> None:
        """psql/pg_restore argv must not contain the DSN password."""
        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(
                app,
                [
                    "maintenance",
                    "restore-hive",
                    "--dsn",
                    self.DSN_WITH_SECRET,
                    "backup.sql",
                ],
            )
            assert result.exit_code == 0
            cmd = mock_run.call_args[0][0]
            assert all(self.SECRET not in arg for arg in cmd), f"Password leaked into argv: {cmd}"

    @pytest.mark.requires_cli
    def test_restore_hive_pgpassword_in_env(self) -> None:
        """psql must receive PGPASSWORD in env kwarg."""
        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            runner.invoke(
                app,
                [
                    "maintenance",
                    "restore-hive",
                    "--dsn",
                    self.DSN_WITH_SECRET,
                    "backup.sql",
                ],
            )
            call_kwargs = mock_run.call_args[1]
            assert "env" in call_kwargs
            assert call_kwargs["env"].get("PGPASSWORD") == self.SECRET

    @pytest.mark.requires_cli
    def test_restore_hive_error_stderr_scrubbed(self) -> None:
        """Password must be scrubbed from restore error output."""
        from subprocess import CalledProcessError

        from typer.testing import CliRunner

        from tapps_brain.cli import app

        runner = CliRunner()
        with patch("subprocess.run") as mock_run:
            err = CalledProcessError(
                1,
                ["psql"],
                stderr=f"FATAL: password authentication failed; dsn={self.DSN_WITH_SECRET}",
            )
            mock_run.side_effect = err
            result = runner.invoke(
                app,
                [
                    "maintenance",
                    "restore-hive",
                    "--dsn",
                    self.DSN_WITH_SECRET,
                    "backup.sql",
                ],
            )
            assert result.exit_code != 0
            assert self.SECRET not in result.output, "Password leaked into error output"
