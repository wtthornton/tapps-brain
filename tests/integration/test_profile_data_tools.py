"""Integration tests — EPIC-075 profile-scoped learned data round-trip."""

from __future__ import annotations

import os
import uuid

import pytest

from tapps_brain.services import profile_data_service

pytestmark = pytest.mark.requires_postgres

_PG_DSN = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")


@pytest.fixture(scope="module", autouse=True)
def _allow_privileged_dev_role() -> None:
    prev = os.environ.get("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE")
    os.environ["TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE"] = "1"
    yield
    if prev is None:
        os.environ.pop("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE", None)
    else:
        os.environ["TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE"] = prev


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _make_cm() -> object:
    from tapps_brain.postgres_connection import PostgresConnectionManager

    return PostgresConnectionManager(_PG_DSN, min_size=1, max_size=3)


@pytest.fixture(scope="module", autouse=True)
def _migrations() -> None:
    _apply_migrations()


@pytest.fixture()
def scope() -> tuple[object, str]:
    cm = _make_cm()
    project_id = f"test-proj-{uuid.uuid4().hex[:8]}"
    return cm, project_id


def test_profile_data_set_get_round_trip(scope: tuple[object, str]) -> None:
    cm, project_id = scope
    weights = {"python": 1.15, "security": 0.9}

    wrote = profile_data_service.profile_data_set(
        cm,
        project_id,
        profile_name="repo-brain",
        data_key="domain_weights",
        value_json=weights,
    )
    assert wrote == {"ok": True}

    read = profile_data_service.profile_data_get(
        cm,
        project_id,
        profile_name="repo-brain",
        data_key="domain_weights",
    )
    assert read["ok"] is True
    assert read["value_json"] == weights
