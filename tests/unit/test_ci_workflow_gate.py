"""TAP-5459: the CI test job must gate the whole integration + compat suite.

The job used to name 19 mock-based integration files explicitly and exclude
every DB-dependent one. The stated reason was that an explicit list stops a new
red test riding in under a directory glob — but the same list is what let
TAP-2727's KG status-code drift, and later a red ``test_profile_filter.py``,
sit on ``main``. An allowlist that excludes most of the suite protects nothing.

These tests pin the widened gate so it cannot be quietly narrowed again, and
pin the role provisioning the DB-dependent tests need in order to run at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI = _REPO_ROOT / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def test_job() -> dict[str, Any]:
    job = yaml.safe_load(_CI.read_text())["jobs"]["test"]
    assert isinstance(job, dict)
    return job


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return list(job.get("steps", []))


def _step_index(job: dict[str, Any], needle: str) -> int:
    for i, step in enumerate(_steps(job)):
        if needle in (step.get("name") or "") or needle in (step.get("run") or ""):
            return i
    raise AssertionError(f"no CI step matching {needle!r}")


class TestSuiteIsGatedWholesale:
    def test_integration_directory_is_run_not_a_file_list(self, test_job: dict[str, Any]) -> None:
        run = _steps(test_job)[_step_index(test_job, "tests/integration/")]["run"]
        assert "tests/integration/ " in run or run.rstrip().endswith("tests/integration/"), (
            "CI must run the whole tests/integration/ directory. A partial file "
            "list silently excludes new tests — the failure mode of TAP-5731."
        )

    def test_compat_suite_is_gated(self, test_job: dict[str, Any]) -> None:
        run = _steps(test_job)[_step_index(test_job, "tests/integration/")]["run"]
        assert "tests/compat/" in run, (
            "the compat/openapi-snapshot suite was excluded under TAP-2803 and "
            "now passes; leaving it ungated lets snapshot drift reach main"
        )

    def test_no_per_file_integration_allowlist_remains(self, test_job: dict[str, Any]) -> None:
        """A reintroduced explicit list is the regression this guards against."""
        run = _steps(test_job)[_step_index(test_job, "tests/integration/")]["run"]
        named_files = [
            tok for tok in run.split() if tok.startswith("tests/") and tok.endswith(".py")
        ]
        assert not named_files, (
            f"CI names individual integration files again: {named_files}. Gate the "
            f"directory and let a test that cannot run skip behind its own guard, "
            f"so the reason lives with the test rather than in the workflow."
        )


class TestRuntimeRoleProvisioning:
    """Without this the DB-dependent tests fail on password authentication."""

    def test_roles_are_provisioned(self, test_job: dict[str, Any]) -> None:
        run = _steps(test_job)[_step_index(test_job, "Provision runtime roles")]["run"]
        assert "001_db_roles.sql" in run, (
            "CI must apply the same roles file the production sidecar applies, "
            "not restate the role definitions"
        )
        assert "ALTER ROLE tapps_runtime" in run and "PASSWORD" in run, (
            "roles/001 creates tapps_runtime with LOGIN but no password; without "
            "an explicit password the RLS tests fail on authentication, which "
            "surfaces as psycopg_pool.PoolTimeout and reads as a pool bug"
        )

    def test_roles_are_provisioned_before_tests_run(self, test_job: dict[str, Any]) -> None:
        """Ordering is load-bearing: after the tests it would provision nothing."""
        assert _step_index(test_job, "Provision runtime roles") < _step_index(
            test_job, "tests/integration/"
        )

    def test_roles_are_provisioned_after_schema_migrations(self, test_job: dict[str, Any]) -> None:
        """roles/001 grants on ALL TABLES, so the tables must already exist."""
        assert _step_index(test_job, "Apply schema migrations") < _step_index(
            test_job, "Provision runtime roles"
        ), (
            "roles/001 issues schema-wide GRANTs against existing objects; run it "
            "before the migrations and every table added later gets no grant"
        )
