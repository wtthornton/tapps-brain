"""TAP-5846: the documented dev-DB setup path must provision ``tapps_runtime``.

``make brain-up`` + ``make brain-migrate`` used to leave the database without a
usable ``tapps_runtime`` login, so every RLS / tenant-isolation integration test
failed on password authentication — 25 failures that read as code regressions
but were entirely environmental.

These tests run without Docker or Postgres: they assert the Makefile wiring and
the credential coupling between the Makefile and the integration tests.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MAKEFILE = _REPO_ROOT / "Makefile"
_ROLES_SQL = _REPO_ROOT / "src" / "tapps_brain" / "migrations" / "roles" / "001_db_roles.sql"

# Integration tests that build a runtime DSN by string-replacing the owner
# credential. Each must agree with the password the Makefile provisions.
_RUNTIME_DSN_TESTS = (
    "tests/integration/test_tenant_isolation.py",
    "tests/integration/test_session_context_persistence.py",
)


@pytest.fixture(scope="module")
def makefile() -> str:
    return _MAKEFILE.read_text()


def _recipe(makefile: str, target: str) -> str:
    """Return the recipe body for *target* (its indented lines)."""
    match = re.search(rf"^{re.escape(target)}:.*?$\n((?:[\t ].*\n|\n)*)", makefile, re.MULTILINE)
    assert match, f"Makefile has no target named {target!r}"
    return match.group(1)


class TestRuntimeRoleProvisioning:
    def test_brain_roles_target_exists(self, makefile: str) -> None:
        """A named target provisions the roles, so it is runnable on its own."""
        assert re.search(r"^brain-roles:", makefile, re.MULTILINE), (
            "Makefile must expose a `brain-roles` target — the RLS tests are "
            "unrunnable locally without it (TAP-5846)"
        )

    def test_brain_roles_applies_the_roles_sql(self, makefile: str) -> None:
        """It applies the same roles file the production sidecar applies."""
        recipe = _recipe(makefile, "brain-roles")
        assert "001_db_roles.sql" in recipe, (
            "brain-roles must apply migrations/roles/001_db_roles.sql rather than "
            "hand-rolling a second, drifting copy of the role definitions"
        )

    def test_brain_roles_sets_the_runtime_password(self, makefile: str) -> None:
        """The roles SQL creates the role WITHOUT a password; the recipe must set one."""
        assert "PASSWORD" not in _ROLES_SQL.read_text().upper(), (
            "roles/001 is expected to stay password-free — passwords are supplied "
            "per-environment. If that changed, this test's premise needs revisiting."
        )
        recipe = _recipe(makefile, "brain-roles")
        assert "ALTER ROLE tapps_runtime" in recipe and "PASSWORD" in recipe, (
            "brain-roles must set a password on tapps_runtime; CREATE ROLE ... LOGIN "
            "alone leaves password authentication failing"
        )

    def test_brain_migrate_provisions_roles(self, makefile: str) -> None:
        """The documented one-step setup path must not stop at schema migrations."""
        recipe = _recipe(makefile, "brain-migrate")
        assert "brain-roles" in recipe, (
            "make brain-migrate must also provision roles, or the documented dev "
            "setup path still yields a DB the RLS tests cannot authenticate against"
        )


class TestDevPasswordMatchesIntegrationTests:
    """The Makefile password and the tests' string-replacement must agree."""

    def test_makefile_declares_the_dev_runtime_password(self, makefile: str) -> None:
        match = re.search(r"^TAPPS_DEV_RUNTIME_PASSWORD \?= (\S+)", makefile, re.MULTILINE)
        assert match, "Makefile must declare TAPPS_DEV_RUNTIME_PASSWORD"
        assert match.group(1) == "tapps_runtime"

    @pytest.mark.parametrize("test_path", _RUNTIME_DSN_TESTS)
    def test_integration_tests_expect_that_password(self, makefile: str, test_path: str) -> None:
        """A change to either side must fail here rather than as a pool timeout."""
        match = re.search(r"^TAPPS_DEV_RUNTIME_PASSWORD \?= (\S+)", makefile, re.MULTILINE)
        assert match
        password = match.group(1)

        source = (_REPO_ROOT / test_path).read_text()
        expected = f'"tapps_runtime:{password}@"'
        assert expected in source, (
            f"{test_path} derives its runtime DSN by replacing the owner credential, "
            f"but does not expect {expected}. The Makefile provisions "
            f"tapps_runtime/{password}; a mismatch surfaces as PoolTimeout, which "
            f"reads as a pool bug rather than a credential mismatch (TAP-5846)."
        )


class TestDevPortGuard:
    def test_check_dev_dsn_target_exists(self, makefile: str) -> None:
        assert re.search(r"^check-dev-dsn:", makefile, re.MULTILINE), (
            "TAPPS_DEV_PORT defaults to 5432, which is occupied on most dev hosts; "
            "a guard must catch a DSN pointing at an unrelated database"
        )

    def test_check_dev_dsn_compares_against_the_published_port(self, makefile: str) -> None:
        recipe = _recipe(makefile, "check-dev-dsn")
        assert "port tapps-brain-db 5432" in recipe, (
            "the guard must resolve the port the dev container actually publishes; "
            "anything else re-derives the assumption it is meant to check"
        )
        assert "TAPPS_DEV_PORT" in recipe

    @pytest.mark.parametrize("target", ["brain-migrate", "brain-roles"])
    def test_dsn_consumers_run_the_guard(self, makefile: str, target: str) -> None:
        """Every target that writes through TAPPS_DEV_DSN checks it first."""
        assert "check-dev-dsn" in _recipe(makefile, target), (
            f"{target} writes to TAPPS_DEV_DSN and must run check-dev-dsn first"
        )
