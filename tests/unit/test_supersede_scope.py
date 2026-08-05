"""Key-scoped supersede — `supersede: "global" | "key-scoped"`.

Cross-key supersede is intended: a save closes the validity interval of a
textually similar entry in the same tier whatever key it lives under. That is
correct for competing claims about one fact, and wrong for a key-space holding
independent facts (one row per distinct thing), where topical similarity
between neighbours is expected.

Reported by nlt-ideas-scout, whose research dossiers are one-per-candidate and
were evicting each other because siblings from one discovery brief are
topically adjacent by construction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tapps_brain.services import memory_service
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path

# Two dossier-shaped values about one theme. Distinct facts, high token overlap
# — the shape that trips the 0.6 default threshold.
_SIBLING_A = (
    "Ask HN: how are teams keeping AI coding agents from burning money on "
    "long autonomous runs? Discussion covers token budgets, model routing, "
    "and per-task spend caps for AI coding agents."
)
_SIBLING_B = (
    "Safety report: AI coding assistant guardrails. Covers token budgets, "
    "model routing, and per-task spend caps for AI coding agents on long "
    "autonomous runs, plus review gates."
)


def _pin_context_threshold(store: MemoryStore, threshold: float = 0.6) -> None:
    """Pin the context-tier conflict threshold so these tests do not depend on
    whichever profile happens to load.

    The bundled ``repo-brain`` profile sets ``per_tier.context: 0.85``; the
    fixture pair below scores 0.71, so without this the conflict never fires
    and every assertion about supersede passes vacuously.
    """
    assert store._profile is not None, "expected a profile to be loaded"
    cc = store._profile.conflict_check
    cc.per_tier = dict(cc.per_tier or {}, context=threshold)


def _save(store: MemoryStore, key: str, value: str, **kw: object) -> dict:
    return memory_service.memory_save(
        store, "proj", "agent-1", key=key, value=value, tier="context", **kw
    )


class TestKeyScopedSupersede:
    def test_key_scoped_leaves_the_neighbour_live(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        _pin_context_threshold(store)
        try:
            _save(store, "cand-a", _SIBLING_A, supersede="key-scoped")
            res = _save(store, "cand-b", _SIBLING_B, supersede="key-scoped")

            assert res["status"] == "saved"
            assert not res.get("invalidated"), res.get("invalidated")
            assert store.get("cand-a") is not None, "neighbour was evicted"
            assert store.get("cand-b") is not None
        finally:
            store.close()

    def test_global_is_unchanged_and_still_supersedes(self, tmp_path: Path) -> None:
        """The default must keep its current behaviour, or this is a silent
        contract change for every other consumer."""
        store = MemoryStore(tmp_path)
        _pin_context_threshold(store)
        try:
            _save(store, "cand-a", _SIBLING_A)
            res = _save(store, "cand-b", _SIBLING_B)
            assert res["status"] == "saved"
            # The neighbour is reported as invalidated under the default.
            assert "cand-a" in res.get("invalidated", [])
        finally:
            store.close()

    def test_omitting_supersede_matches_explicit_global(self, tmp_path: Path) -> None:
        store_a = MemoryStore(tmp_path / "a")
        store_b = MemoryStore(tmp_path / "b")
        _pin_context_threshold(store_a)
        _pin_context_threshold(store_b)
        try:
            _save(store_a, "cand-a", _SIBLING_A)
            omitted = _save(store_a, "cand-b", _SIBLING_B)

            _save(store_b, "cand-a", _SIBLING_A, supersede="global")
            explicit = _save(store_b, "cand-b", _SIBLING_B, supersede="global")

            assert omitted.get("invalidated") == explicit.get("invalidated")
        finally:
            store_a.close()
            store_b.close()

    def test_key_scoped_still_replaces_its_own_key(self, tmp_path: Path) -> None:
        """key-scoped narrows which *neighbours* are touched — it must not stop
        a key from updating its own value."""
        store = MemoryStore(tmp_path)
        try:
            _save(store, "cand-a", _SIBLING_A, supersede="key-scoped")
            _save(store, "cand-a", _SIBLING_B, supersede="key-scoped")
            entry = store.get("cand-a")
            assert entry is not None
            assert entry.value == _SIBLING_B
        finally:
            store.close()


class TestSupersedeValidation:
    def test_unknown_mode_is_rejected(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        try:
            res = _save(store, "k1", "some value", supersede="key_scoped")
            assert res["error"] == "bad_request"
            assert "supersede" in res["detail"]
        finally:
            store.close()

    def test_rejected_mode_does_not_write(self, tmp_path: Path) -> None:
        """Fail closed: a typo must not fall back to global and supersede
        neighbours the caller asked to protect."""
        store = MemoryStore(tmp_path)
        try:
            _save(store, "k1", "some value")
            _save(store, "k2", "other value", supersede="nonsense")
            assert store.get("k2") is None
        finally:
            store.close()


class TestBatchSupersede:
    def test_batch_honours_key_scoped(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        _pin_context_threshold(store)
        try:
            res = memory_service.memory_save_many(
                store,
                "proj",
                "agent-1",
                entries=[
                    {"key": "cand-a", "value": _SIBLING_A, "tier": "context"},
                    {"key": "cand-b", "value": _SIBLING_B, "tier": "context"},
                ],
                supersede="key-scoped",
            )
            assert res["error_count"] == 0
            assert store.get("cand-a") is not None, "neighbour evicted in batch"
            assert store.get("cand-b") is not None
        finally:
            store.close()

    def test_batch_rejects_unknown_mode(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        try:
            res = memory_service.memory_save_many(
                store,
                "proj",
                "agent-1",
                entries=[{"key": "k1", "value": "v1"}],
                supersede="bogus",
            )
            assert res["error"] == "bad_request"
        finally:
            store.close()
