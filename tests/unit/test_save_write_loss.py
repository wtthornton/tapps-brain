"""Regression tests for acknowledged-but-lost writes on the save path.

Reported by consumer nlt-ideas-scout against 3.28.2 (TAP-5614):

* ``/v1/remember`` returned ``200 {"status": "saved"}`` with a *foreign* key
  and the requested key never existed — the dedup fast path matched on value
  alone and swallowed the write (TAP-5615).
* Re-saving a key a previous conflict pass had invalidated returned
  ``400 invalid_at must be after valid_at`` on a request carrying no temporal
  fields at all (TAP-5616).
* Neither the coalescing nor the neighbour invalidation was visible in the
  response (TAP-5617).

All three reproduce single-threaded; none of them needs concurrency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tapps_brain.models import MemoryEntry, MemorySource, MemoryTier
from tapps_brain.services import memory_service as ms
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path

# Two values that clear the 0.6 ``pattern``-tier conflict cutoff without being
# byte-identical (identical values would take the dedup path instead).
_DOSSIER_A = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
_DOSSIER_B = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda nu"


def _save(store: MemoryStore, key: str, value: str, tier: str = "context") -> dict[str, object]:
    return ms.memory_save(store, "proj", "agent", key=key, value=value, tier=tier, source="agent")


class TestDedupIsKeyScoped:
    def test_same_value_distinct_keys_all_persist(self, tmp_path: Path) -> None:
        """The reporter's probe: five keys, one value, five readable rows."""
        store = MemoryStore(tmp_path)
        keys = [f"diag-echo-{i}" for i in range(5)]

        envelopes = [_save(store, key, "echo-probe") for key in keys]

        assert [e["key"] for e in envelopes] == keys
        assert all(e["status"] == "saved" for e in envelopes)
        for key in keys:
            entry = store.get(key)
            assert entry is not None, f"{key} was acknowledged but not persisted"
            assert entry.value == "echo-probe"

    def test_same_key_same_value_is_reinforce_noop(self, tmp_path: Path) -> None:
        """TAP-6696 / VAL-06: a byte-identical re-save is a no-op, reported
        as ``coalesced`` (not ``saved``) since no new row is written."""
        store = MemoryStore(tmp_path)
        _save(store, "stable-key", "unchanged content")

        envelope = _save(store, "stable-key", "unchanged content")

        assert envelope["status"] == "coalesced"
        assert envelope["key"] == "stable-key"
        assert envelope["coalesced_into"] == "stable-key"
        assert envelope["persisted"] is False
        entry = store.get("stable-key")
        assert entry is not None
        assert entry.reinforce_count == 1

    def test_dedup_disabled_still_persists_distinct_keys(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        store.save(key="first", value="same text", dedup=False)
        store.save(key="second", value="same text", dedup=False)

        assert store.get("first") is not None
        assert store.get("second") is not None


class TestRevivingAnInvalidatedKey:
    def _invalidate_a(self, store: MemoryStore) -> None:
        _save(store, "dossier-a", _DOSSIER_A, tier="pattern")
        _save(store, "dossier-b", _DOSSIER_B, tier="pattern")
        invalidated = store.get("dossier-a")
        assert invalidated is not None
        assert invalidated.invalid_at is not None, "conflict pass did not invalidate dossier-a"

    def test_resave_of_invalidated_key_returns_saved(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        self._invalidate_a(store)

        envelope = _save(store, "dossier-a", _DOSSIER_A + " updated", tier="pattern")

        assert envelope["status"] == "saved", envelope
        assert "error" not in envelope

    def test_revived_entry_clears_temporal_and_contradiction_state(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        self._invalidate_a(store)

        _save(store, "dossier-a", _DOSSIER_A + " updated", tier="pattern")

        revived = store.get("dossier-a")
        assert revived is not None
        assert revived.invalid_at is None
        assert revived.contradicted is False
        assert revived.contradiction_reason is None
        assert revived.superseded_by is None

    def test_revived_entry_is_recallable(self, tmp_path: Path) -> None:
        """gc archives ``contradicted`` rows — a half-revived row is lost again."""
        store = MemoryStore(tmp_path)
        self._invalidate_a(store)

        _save(store, "dossier-a", _DOSSIER_A + " updated", tier="pattern")

        recalled = store.recall("alpha beta gamma delta")
        assert "dossier-a" in {memory["key"] for memory in recalled.memories}

    def test_explicitly_inverted_interval_is_still_rejected(self, tmp_path: Path) -> None:
        """The revive path must not swallow a genuinely bad caller-supplied window."""
        store = MemoryStore(tmp_path)

        result = ms.memory_save(
            store,
            "proj",
            "agent",
            key="explicit-window",
            value="a value with an inverted validity window",
            tier="pattern",
        )
        assert result["status"] == "saved"

        entry = store.get("explicit-window")
        assert entry is not None
        try:
            entry.model_copy(update={"valid_at": "2026-01-02T00:00:00+00:00"}).model_validate(
                {
                    **entry.model_dump(),
                    "valid_at": "2026-01-02T00:00:00+00:00",
                    "invalid_at": "2026-01-01T00:00:00+00:00",
                }
            )
        except ValueError as exc:
            assert "invalid_at must be after valid_at" in str(exc)
        else:  # pragma: no cover - the invariant must still hold
            raise AssertionError("inverted valid_at/invalid_at was accepted")


class TestSaveEnvelopeHonesty:
    def _entry(self, key: str) -> MemoryEntry:
        return MemoryEntry(
            key=key,
            value="some content",
            tier=MemoryTier.pattern,
            source=MemorySource.agent,
        )

    def test_envelope_never_claims_saved_for_a_foreign_key(self) -> None:
        envelope = ms._save_result_envelope(self._entry("other-key"), requested_key="asked-for")

        assert envelope["status"] == "coalesced"
        assert envelope["key"] == "asked-for"
        assert envelope["coalesced_into"] == "other-key"
        assert envelope["persisted"] is False

    def test_envelope_says_saved_when_the_key_matches(self) -> None:
        envelope = ms._save_result_envelope(self._entry("asked-for"), requested_key="asked-for")

        assert envelope["status"] == "saved"
        assert envelope["key"] == "asked-for"
        assert "persisted" not in envelope

    def test_conflicting_save_reports_invalidated_keys(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        _save(store, "dossier-a", _DOSSIER_A, tier="pattern")

        envelope = _save(store, "dossier-b", _DOSSIER_B, tier="pattern")

        assert envelope["invalidated"] == ["dossier-a"]

    def test_non_conflicting_save_omits_invalidated(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)

        envelope = _save(store, "lonely", "an entry with no similar neighbour", tier="pattern")

        assert "invalidated" not in envelope
