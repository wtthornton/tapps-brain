"""TAP-5509: the ledger check — would this edge violate declared cardinality?

`brain_get_neighbors` and `brain_query_events` are retrieval. This is a
decision, asked *before* a side effect, so a semantic firewall can refuse an
action and say why. It must be read-only and it must never guess.
"""

from __future__ import annotations

from typing import Any

import pytest

from tapps_brain.services import kg_service
from tapps_brain.services.kg_service import (
    CHECK_ALREADY_ASSERTED,
    CHECK_EXCEEDED,
    CHECK_NO_SUBJECT,
    CHECK_UNBOUNDED,
    CHECK_UNREGISTERED,
    CHECK_WITHIN_LIMIT,
)


class _FakeKGStore:
    """Stands in for the KG store; records every call so writes are detectable."""

    def __init__(
        self,
        *,
        declaration: dict[str, Any] | None = None,
        entities: dict[str, list[dict[str, Any]]] | None = None,
        object_count: int = 0,
        existing_edge: bool = False,
    ) -> None:
        self._declaration = declaration
        self._entities = entities or {}
        self._object_count = object_count
        self._existing_edge = existing_edge
        self.calls: list[str] = []

    def get_predicate(self, predicate: str) -> dict[str, Any] | None:
        self.calls.append("get_predicate")
        return self._declaration

    def find_entities_by_name(self, canonical_name: str) -> list[dict[str, Any]]:
        self.calls.append("find_entities_by_name")
        return self._entities.get(canonical_name, [])

    def count_active_objects(self, *, subject_entity_id: str, predicate: str) -> int:
        self.calls.append("count_active_objects")
        return self._object_count

    def active_edge_exists(
        self, *, subject_entity_id: str, predicate: str, object_entity_id: str
    ) -> bool:
        self.calls.append("active_edge_exists")
        return self._existing_edge

    # Any of these being called would mean the check wrote something.
    def register_predicate(self, **_kw: Any) -> dict[str, Any]:  # pragma: no cover
        self.calls.append("register_predicate")
        raise AssertionError("kg_check must not write")

    def upsert_edge(self, **_kw: Any) -> str:  # pragma: no cover
        self.calls.append("upsert_edge")
        raise AssertionError("kg_check must not write")


def _entity(entity_id: str, entity_type: str, name: str) -> dict[str, Any]:
    return {"entity_id": entity_id, "entity_type": entity_type, "canonical_name": name}


def _check(store: _FakeKGStore, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> dict[str, Any]:
    monkeypatch.setattr(kg_service, "_kg_store", lambda *_a, **_kw: store)
    params: dict[str, Any] = {"subject_key": "order-1", "predicate": "refunded"}
    params.update(kwargs)
    return kg_service.kg_check(None, "proj", "brain", **params)


class TestOpenByDefault:
    def test_unregistered_predicate_allows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A check that denied everything undeclared would make registration
        mandatory through the back door."""
        result = _check(_FakeKGStore(declaration=None), monkeypatch)
        assert result["decision"] == "allow"
        assert result["reason"] == CHECK_UNREGISTERED

    def test_predicate_without_max_count_allows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _FakeKGStore(declaration={"predicate": "refunded", "max_count": None})
        result = _check(store, monkeypatch)
        assert result["decision"] == "allow"
        assert result["reason"] == CHECK_UNBOUNDED
        assert result["max_count"] is None

    def test_unknown_subject_allows_with_zero_count(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A subject with no edges cannot be over its limit."""
        store = _FakeKGStore(declaration={"max_count": 1}, entities={})
        result = _check(store, monkeypatch)
        assert result["decision"] == "allow"
        assert result["reason"] == CHECK_NO_SUBJECT
        assert result["count"] == 0


class TestDeny:
    def test_at_the_limit_denies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _FakeKGStore(
            declaration={"max_count": 1},
            entities={"order-1": [_entity("e1", "order", "order-1")]},
            object_count=1,
        )
        result = _check(store, monkeypatch)
        assert result["decision"] == "deny"
        assert result["reason"] == CHECK_EXCEEDED
        assert result["count"] == 1
        assert result["max_count"] == 1

    def test_over_the_limit_denies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _FakeKGStore(
            declaration={"max_count": 2},
            entities={"order-1": [_entity("e1", "order", "order-1")]},
            object_count=5,
        )
        assert _check(store, monkeypatch)["decision"] == "deny"

    def test_deny_carries_the_numbers_that_produced_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A firewall has to explain a refusal, not just report one."""
        store = _FakeKGStore(
            declaration={"max_count": 1},
            entities={"order-1": [_entity("e1", "order", "order-1")]},
            object_count=1,
        )
        result = _check(store, monkeypatch)
        assert "order-1" in result["detail"]
        assert "refunded" in result["detail"]
        assert result["predicate"] == "refunded"
        assert result["subject_key"] == "order-1"


class TestAllowWithinLimit:
    def test_below_the_limit_allows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _FakeKGStore(
            declaration={"max_count": 3},
            entities={"order-1": [_entity("e1", "order", "order-1")]},
            object_count=1,
        )
        result = _check(store, monkeypatch)
        assert result["decision"] == "allow"
        assert result["reason"] == CHECK_WITHIN_LIMIT
        assert result["count"] == 1
        assert result["max_count"] == 3


class TestIdempotentReassert:
    def test_reasserting_an_existing_edge_allows_at_the_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-asserting adds no object; denying it would make retries unsafe."""
        store = _FakeKGStore(
            declaration={"max_count": 1},
            entities={
                "order-1": [_entity("e1", "order", "order-1")],
                "pay-9": [_entity("e2", "payment", "pay-9")],
            },
            object_count=1,
            existing_edge=True,
        )
        result = _check(store, monkeypatch, object_key="pay-9")
        assert result["decision"] == "allow"
        assert result["reason"] == CHECK_ALREADY_ASSERTED

    def test_a_different_object_at_the_limit_still_denies(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _FakeKGStore(
            declaration={"max_count": 1},
            entities={
                "order-1": [_entity("e1", "order", "order-1")],
                "pay-other": [_entity("e3", "payment", "pay-other")],
            },
            object_count=1,
            existing_edge=False,
        )
        result = _check(store, monkeypatch, object_key="pay-other")
        assert result["decision"] == "deny"

    def test_unknown_object_does_not_count_as_an_existing_edge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _FakeKGStore(
            declaration={"max_count": 1},
            entities={"order-1": [_entity("e1", "order", "order-1")]},
            object_count=1,
        )
        result = _check(store, monkeypatch, object_key="never-seen")
        assert result["decision"] == "deny"


class TestAmbiguityIsNotGuessed:
    def test_ambiguous_subject_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Two types sharing a name is a question only the caller can answer —
        guessing hands a firewall a verdict about the wrong entity."""
        store = _FakeKGStore(
            declaration={"max_count": 1},
            entities={
                "order-1": [
                    _entity("e1", "order", "order-1"),
                    _entity("e2", "invoice", "order-1"),
                ]
            },
        )
        result = _check(store, monkeypatch)
        assert result["error"] == "invalid_request"
        assert "subject_type" in result["message"]

    def test_subject_type_disambiguates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _FakeKGStore(
            declaration={"max_count": 1},
            entities={
                "order-1": [
                    _entity("e1", "order", "order-1"),
                    _entity("e2", "invoice", "order-1"),
                ]
            },
            object_count=0,
        )
        result = _check(store, monkeypatch, subject_type="order")
        assert result["decision"] == "allow"
        assert result["reason"] == CHECK_WITHIN_LIMIT


class TestValidation:
    @pytest.mark.parametrize("bad", ["", "   "])
    def test_blank_subject_key_is_rejected(self, bad: str, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _check(_FakeKGStore(), monkeypatch, subject_key=bad)
        assert result["error"] == "invalid_request"

    @pytest.mark.parametrize("bad", ["", "   "])
    def test_blank_predicate_is_rejected(self, bad: str, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _check(_FakeKGStore(), monkeypatch, predicate=bad)
        assert result["error"] == "invalid_request"


class TestReadOnly:
    def test_check_never_writes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The whole point is asking before the side effect."""
        store = _FakeKGStore(
            declaration={"max_count": 1},
            entities={"order-1": [_entity("e1", "order", "order-1")]},
            object_count=1,
        )
        _check(store, monkeypatch)
        assert "upsert_edge" not in store.calls
        assert "register_predicate" not in store.calls

    def test_unregistered_predicate_short_circuits_before_lookups(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No point resolving entities for a predicate with nothing to enforce."""
        store = _FakeKGStore(declaration=None)
        _check(store, monkeypatch)
        assert store.calls == ["get_predicate"]
