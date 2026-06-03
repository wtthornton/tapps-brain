"""Unit tests for MemoryStore.verify_integrity() (H4b)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tapps_brain.integrity import reset_key_cache
from tapps_brain.models import MemoryEntry, MemorySource, MemoryTier
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _reset_integrity_key() -> Generator[None, None, None]:
    """Reset the cached signing key before and after each test."""
    reset_key_cache()
    yield
    reset_key_cache()


@pytest.fixture()
def store(tmp_path: Path) -> Generator[MemoryStore, None, None]:
    """Create a MemoryStore backed by a temp directory; close on teardown."""
    s = MemoryStore(tmp_path)
    yield s
    s.close()


class TestVerifyIntegrity:
    """Tests for MemoryStore.verify_integrity()."""

    def test_empty_store_returns_zeroes(self, store: MemoryStore) -> None:
        result = store.verify_integrity()
        assert result["total"] == 0
        assert result["verified"] == 0
        assert result["tampered"] == 0
        assert result["no_hash"] == 0
        assert result["tampered_keys"] == []
        assert result["missing_hash_keys"] == []
        assert result["tampered_details"] == []

    def test_valid_entries_pass(self, store: MemoryStore) -> None:
        store.save(
            "entry-a", "Architecture uses event sourcing", tier="architectural", source="human"
        )
        store.save("entry-b", "Use pytest for all tests", tier="pattern", source="agent")
        result = store.verify_integrity()
        assert result["total"] == 2
        assert result["verified"] == 2
        assert result["tampered"] == 0
        assert result["no_hash"] == 0

    def test_tampered_value_detected(self, store: MemoryStore) -> None:
        store.save("entry-a", "Original value", tier="pattern", source="agent")

        # Tamper with the value directly in the in-memory cache
        with store._lock:
            cached = store._entries["entry-a"]
            tampered = cached.model_copy(update={"value": "TAMPERED VALUE"})
            store._entries["entry-a"] = tampered

        result = store.verify_integrity()
        assert result["tampered"] == 1
        assert result["tampered_keys"] == ["entry-a"]
        assert result["verified"] == 0
        assert len(result["tampered_details"]) == 1
        assert result["tampered_details"][0]["key"] == "entry-a"

    def test_missing_hash_reported(self, store: MemoryStore) -> None:
        # Insert an entry without an integrity hash (simulating pre-v8 data)
        entry = MemoryEntry(
            key="legacy-entry",
            value="Old entry without hash",
            tier=MemoryTier.context,
            source=MemorySource.agent,
            integrity_hash=None,
        )
        with store._lock:
            store._entries[entry.key] = entry

        result = store.verify_integrity()
        assert result["total"] == 1
        assert result["no_hash"] == 1
        assert "legacy-entry" in result["missing_hash_keys"]
        assert result["verified"] == 0
        assert result["tampered"] == 0

    def test_mixed_valid_tampered_missing(self, store: MemoryStore) -> None:
        # Valid entry
        store.save("valid-key", "Valid value", tier="pattern", source="agent")

        # Entry with missing hash
        no_hash = MemoryEntry(
            key="no-hash-key",
            value="Missing hash",
            tier=MemoryTier.context,
            source=MemorySource.agent,
            integrity_hash=None,
        )
        with store._lock:
            store._entries[no_hash.key] = no_hash

        # Tampered entry
        store.save("tamper-key", "Original value", tier="pattern", source="agent")
        with store._lock:
            cached = store._entries["tamper-key"]
            tampered = cached.model_copy(update={"value": "CHANGED"})
            store._entries["tamper-key"] = tampered

        result = store.verify_integrity()
        assert result["total"] == 3
        assert result["verified"] == 1
        assert result["tampered"] == 1
        assert result["no_hash"] == 1

    def test_tampered_tier_detected(self, store: MemoryStore) -> None:
        store.save("tier-test", "Some value", tier="pattern", source="agent")
        with store._lock:
            cached = store._entries["tier-test"]
            tampered = cached.model_copy(update={"tier": MemoryTier.architectural})
            store._entries["tier-test"] = tampered

        result = store.verify_integrity()
        assert result["tampered"] == 1
        assert result["tampered_keys"] == ["tier-test"]

    def test_tampered_source_detected(self, store: MemoryStore) -> None:
        store.save("source-test", "Some value", tier="pattern", source="agent")
        with store._lock:
            cached = store._entries["source-test"]
            tampered = cached.model_copy(update={"source": MemorySource.human})
            store._entries["source-test"] = tampered

        result = store.verify_integrity()
        assert result["tampered"] == 1

    def test_tampered_details_include_hashes(self, store: MemoryStore) -> None:
        store.save("hash-detail", "Original", tier="pattern", source="agent")
        with store._lock:
            cached = store._entries["hash-detail"]
            original_hash = cached.integrity_hash
            tampered = cached.model_copy(update={"value": "Modified"})
            store._entries["hash-detail"] = tampered

        result = store.verify_integrity()
        assert result["tampered"] == 1
        detail = result["tampered_details"][0]
        assert detail["key"] == "hash-detail"
        assert detail["stored_hash"] == original_hash
        assert detail["expected_hash"] != original_hash


class TestRehashIntegrityV1:
    """TAP-2857: the v1->v2 rehash shim must upgrade *and persist*."""

    @staticmethod
    def _make_legacy_v1(store: MemoryStore, key: str, value: str) -> None:
        """Rewrite an existing entry as a valid legacy ``integrity_hash_v == 1`` row."""
        from tapps_brain.integrity import compute_integrity_hash_v1

        entry = store._entries[key]
        tier_str = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
        source_str = entry.source.value if hasattr(entry.source, "value") else str(entry.source)
        v1_hash = compute_integrity_hash_v1(entry.key, value, tier_str, source_str)
        legacy = entry.model_copy(update={"integrity_hash": v1_hash, "integrity_hash_v": 1})
        store._entries[key] = legacy
        store._persistence.save(legacy)

    def test_rehash_v1_upgrades_in_memory_and_persists_to_backend(self, store: MemoryStore) -> None:
        """A valid v1 row is upgraded to v2 in memory and the upgrade is written
        through to the backend (the TAP-2857 bug previously crashed here and
        never persisted)."""
        from unittest.mock import patch

        from tapps_brain.integrity import INTEGRITY_HASH_VERSION

        store.save(key="legacy", value="legacy value", tier="pattern", source="agent")
        self._make_legacy_v1(store, "legacy", "legacy value")
        backend = store._persistence

        with patch.object(backend, "save", wraps=backend.save) as spy:
            result = store.rehash_integrity_v1()

        assert result["upgraded"] == 1
        assert result["tampered"] == 0
        # In-memory entry is now v2.
        assert store._entries["legacy"].integrity_hash_v == INTEGRITY_HASH_VERSION
        # The upgrade was persisted to the backend with the v2 hash version.
        assert spy.call_count >= 1
        persisted = spy.call_args_list[-1].args[0]
        assert persisted.integrity_hash_v == INTEGRITY_HASH_VERSION

    def test_rehash_v1_skips_already_v2(self, store: MemoryStore) -> None:
        """An already-v2 entry is counted as ``already_v2`` and not re-persisted."""
        store.save(key="modern", value="modern value", tier="pattern", source="agent")
        result = store.rehash_integrity_v1()
        assert result["upgraded"] == 0
        assert result["already_v2"] == 1
