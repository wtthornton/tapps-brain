"""Unit tests for profile_data_service (EPIC-075)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

from tapps_brain.services import profile_data_service


class _FakeCM:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_row: tuple[Any, ...] | None = None

    @contextmanager
    def project_context(self, project_id: str):
        _ = project_id
        cm = self

        class _FakeCursor:
            def execute(self, sql: str, params: tuple[Any, ...]) -> None:
                cm.executed.append((sql, params))

            def fetchone(self) -> tuple[Any, ...] | None:
                return cm.fetch_row

            def __enter__(self) -> _FakeCursor:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        class _FakeConn:
            def cursor(self) -> _FakeCursor:
                return _FakeCursor()

            def commit(self) -> None:
                return None

            def __enter__(self) -> _FakeConn:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        yield _FakeConn()


def test_profile_data_set_requires_profile() -> None:
    out = profile_data_service.profile_data_set(
        MagicMock(), "proj", profile_name="", data_key="k", value_json={}
    )
    assert out["error"] == "bad_request"


def test_profile_data_set_upserts() -> None:
    cm = _FakeCM()
    out = profile_data_service.profile_data_set(
        cm,
        "proj",
        profile_name="repo-brain",
        data_key="domain_weights",
        value_json={"python": 1.2},
    )
    assert out == {"ok": True}
    assert cm.executed
    assert "INSERT INTO profile_scoped_data" in cm.executed[0][0]


def test_profile_data_get_missing() -> None:
    cm = _FakeCM()
    cm.fetch_row = None
    out = profile_data_service.profile_data_get(
        cm, "proj", profile_name="repo-brain", data_key="domain_weights"
    )
    assert out == {"ok": False}


def test_profile_data_get_hit() -> None:
    cm = _FakeCM()
    cm.fetch_row = ({"python": 1.1},)
    out = profile_data_service.profile_data_get(
        cm, "proj", profile_name="repo-brain", data_key="domain_weights"
    )
    assert out == {"ok": True, "value_json": {"python": 1.1}}
