"""TAP-6816: /v1/forget (and brain_forget) must reap the Hive copy it left behind.

Unit-level, no live Postgres: ``FakeHiveBackend`` reproduces the one piece of
real ``PostgresHiveBackend`` behavior these tests depend on — ``invalid_at``
gates both ``search()`` (the Hive-search layer) and ``archive_entry()`` (the
supported removal path) — so "reaped" and "no longer found by Hive search"
are proven through the same mechanism recall actually uses, not asserted
independently of it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from tapps_brain.services.memory_service import async_brain_forget, brain_forget


class FakeHiveBackend:
    """In-memory double for HiveBackend, honoring the invalid_at contract."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.archive_calls: list[tuple[str, str]] = []

    def seed(self, *, namespace: str, key: str, value: str = "v") -> None:
        self._rows[(namespace, key)] = {
            "namespace": namespace,
            "key": key,
            "value": value,
            "invalid_at": None,
        }

    def search(self, query: str, namespaces: list[str] | None = None, **_: Any) -> list[dict]:
        ns = set(namespaces or [])
        return [
            dict(row)
            for (namespace, _key), row in self._rows.items()
            if namespace in ns and row["invalid_at"] is None
        ]

    def get_agent_groups(self, agent_id: str, project_id: str) -> list[str]:
        return []

    def archive_entry(self, namespace: str, key: str) -> bool:
        self.archive_calls.append((namespace, key))
        row = self._rows.get((namespace, key))
        if row is None or row["invalid_at"] is not None:
            return False
        row["invalid_at"] = "2026-09-15T00:00:00+00:00"
        return True


def _make_store(hive: FakeHiveBackend | None, *, has_entry: bool = True) -> Any:
    store = SimpleNamespace()
    store._hive_store = hive
    store._persistence = None
    store._profile = SimpleNamespace(name="repo-brain")
    store._project_id = "proj-1"
    store._hive_agent_id = "agent-1"
    store._deleted: list[str] = []
    store.get = lambda key: SimpleNamespace(key=key) if has_entry else None
    store.delete = lambda key: store._deleted.append(key)
    return store


class TestBrainForgetReapsHive:
    def test_reaps_hive_copy_and_recall_no_longer_finds_it(self) -> None:
        hive = FakeHiveBackend()
        hive.seed(namespace="universal", key="shared-key")
        store = _make_store(hive)

        # Before forget: the Hive-search layer (what recall consults) sees it.
        assert any(r["key"] == "shared-key" for r in hive.search("q", namespaces=["universal"]))

        result = brain_forget(store, "proj-1", "agent-1", key="shared-key")

        assert result == {"forgotten": True, "key": "shared-key", "hive_forgotten": True}
        assert store._deleted == ["shared-key"]
        # b3: absence proven through the recall-layer read path, not the raw table.
        assert not any(r["key"] == "shared-key" for r in hive.search("q", namespaces=["universal"]))

    def test_idempotent_when_no_hive_copy_exists(self) -> None:
        """b5: forgetting a private-only memory must not error on the Hive side."""
        hive = FakeHiveBackend()  # nothing seeded
        store = _make_store(hive)

        result = brain_forget(store, "proj-1", "agent-1", key="private-only")

        assert result == {"forgotten": True, "key": "private-only", "hive_forgotten": False}

    def test_idempotent_with_no_hive_store_attached(self) -> None:
        store = _make_store(hive=None)

        result = brain_forget(store, "proj-1", "agent-1", key="k")

        assert result == {"forgotten": True, "key": "k", "hive_forgotten": False}

    def test_not_found_short_circuits_before_any_hive_call(self) -> None:
        hive = FakeHiveBackend()
        hive.seed(namespace="universal", key="k")
        store = _make_store(hive, has_entry=False)

        result = brain_forget(store, "proj-1", "agent-1", key="k")

        assert result == {"forgotten": False, "reason": "not_found"}
        assert hive.archive_calls == []

    def test_hive_archive_raising_does_not_error_the_forget(self) -> None:
        """A Hive-side failure must not surface as a 500 to the forget caller."""

        class RaisingHive(FakeHiveBackend):
            def archive_entry(self, namespace: str, key: str) -> bool:
                raise RuntimeError("hive unreachable")

        hive = RaisingHive()
        hive.seed(namespace="universal", key="k")
        store = _make_store(hive)

        result = brain_forget(store, "proj-1", "agent-1", key="k")

        assert result == {"forgotten": True, "key": "k", "hive_forgotten": False}

    def test_reaps_across_group_namespaces_the_agent_belongs_to(self) -> None:
        """A key propagated to a group namespace is still found by recall's search set."""

        class GroupHive(FakeHiveBackend):
            def get_agent_groups(self, agent_id: str, project_id: str) -> list[str]:
                return ["frontend-guild"]

        hive = GroupHive()
        hive.seed(namespace="frontend-guild", key="shared-key")
        store = _make_store(hive)

        result = brain_forget(store, "proj-1", "agent-1", key="shared-key")

        assert result["hive_forgotten"] is True
        assert not any(
            r["key"] == "shared-key" for r in hive.search("q", namespaces=["frontend-guild"])
        )


class TestAsyncBrainForgetReapsHive:
    def test_async_forget_reaps_hive_copy(self) -> None:
        hive = FakeHiveBackend()
        hive.seed(namespace="universal", key="shared-key")
        sync_store = _make_store(hive)

        async_store = SimpleNamespace()
        async_store._store = sync_store
        async_store._async_backend = None

        async def _get(key: str) -> Any:
            return sync_store.get(key)

        async def _delete(key: str) -> None:
            sync_store.delete(key)

        async_store.get = _get
        async_store.delete = _delete

        result = asyncio.run(async_brain_forget(async_store, "proj-1", "agent-1", key="shared-key"))

        assert result == {"forgotten": True, "key": "shared-key", "hive_forgotten": True}
        assert not any(r["key"] == "shared-key" for r in hive.search("q", namespaces=["universal"]))


class TestForgetResponseShapeIsAdditiveOnly:
    """VAL-06: /v1/forget has no drift detection of its own — this is it.

    Pins the exact field->type shape for both outcomes. A rename, retype, or
    removal of ``forgotten``/``key``/``reason`` fails here; a new field
    alongside them does not (additive-only contract, TAP-6816 lane doc).
    """

    def test_forgotten_case_shape(self) -> None:
        hive = FakeHiveBackend()
        store = _make_store(hive)

        result = brain_forget(store, "proj-1", "agent-1", key="k1")

        assert set(result.keys()) == {"forgotten", "key", "hive_forgotten"}
        assert result["forgotten"] is True
        assert isinstance(result["key"], str) and result["key"] == "k1"
        assert isinstance(result["hive_forgotten"], bool)

    def test_not_found_case_shape(self) -> None:
        store = _make_store(hive=None, has_entry=False)

        result = brain_forget(store, "proj-1", "agent-1", key="missing")

        assert set(result.keys()) == {"forgotten", "reason"}
        assert result["forgotten"] is False
        assert result["reason"] == "not_found"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
