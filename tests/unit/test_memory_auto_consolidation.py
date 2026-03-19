"""Tests for auto-consolidation triggers (Epic 58, Story 58.3)."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tapps_brain.auto_consolidation import (
    CONSOLIDATION_STATE_FILE,
    ConsolidationResult,
    PeriodicScanResult,
    _get_last_scan_time,
    _update_last_scan_time,
    check_consolidation_on_save,
    run_periodic_consolidation_scan,
    should_run_auto_consolidation,
)
from tapps_brain.models import (
    ConsolidatedEntry,
    MemoryEntry,
    MemoryTier,
)
from tapps_brain.store import ConsolidationConfig, MemoryStore

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_entry(
    key: str,
    value: str,
    *,
    tier: MemoryTier = MemoryTier.pattern,
    confidence: float = 0.7,
    tags: list[str] | None = None,
    updated_at: str | None = None,
) -> MemoryEntry:
    """Helper to create test entries."""
    if updated_at is None:
        updated_at = datetime.now(tz=UTC).isoformat()
    return MemoryEntry(
        key=key,
        value=value,
        tier=tier,
        confidence=confidence,
        tags=tags or [],
        updated_at=updated_at,
    )


@pytest.fixture
def temp_project_root() -> Path:
    """Create a temporary project root directory."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        yield Path(tmp)


@pytest.fixture
def mock_store(temp_project_root: Path) -> MemoryStore:
    """Create a mock memory store with test entries."""
    store = MemoryStore(temp_project_root)
    yield store
    store.close()


@pytest.fixture
def jwt_entries() -> list[MemoryEntry]:
    """A set of related JWT entries for consolidation tests."""
    base_time = datetime.now(tz=UTC)
    return [
        _make_entry(
            key="auth-jwt-config",
            value="Use RS256 for JWT signing. Store keys in environment variables.",
            tier=MemoryTier.architectural,
            confidence=0.9,
            tags=["security", "jwt", "authentication"],
            updated_at=(base_time - timedelta(days=2)).isoformat(),
        ),
        _make_entry(
            key="auth-jwt-tokens",
            value="JWT tokens should use RS256 algorithm. Refresh tokens expire in 7 days.",
            tier=MemoryTier.architectural,
            confidence=0.8,
            tags=["security", "jwt", "tokens"],
            updated_at=(base_time - timedelta(days=1)).isoformat(),
        ),
        _make_entry(
            key="auth-jwt-expiry",
            value="Access tokens expire in 15 minutes. Use sliding window for refresh.",
            tier=MemoryTier.pattern,
            confidence=0.7,
            tags=["security", "jwt", "expiry"],
            updated_at=base_time.isoformat(),
        ),
    ]


# ---------------------------------------------------------------------------
# ConsolidationResult tests
# ---------------------------------------------------------------------------


class TestConsolidationResult:
    """Tests for ConsolidationResult class."""

    def test_default_values(self) -> None:
        """Default values are correctly initialized."""
        result = ConsolidationResult()
        assert result.triggered is False
        assert result.consolidated_entry is None
        assert result.source_keys == []
        assert result.reason == ""

    def test_with_values(self) -> None:
        """Values are correctly stored."""
        entry = ConsolidatedEntry(
            key="test-consolidated",
            value="Test value",
            source_ids=["src-1", "src-2"],
        )
        result = ConsolidationResult(
            triggered=True,
            consolidated_entry=entry,
            source_keys=["src-1", "src-2"],
            reason="similarity",
        )
        assert result.triggered is True
        assert result.consolidated_entry == entry
        assert result.source_keys == ["src-1", "src-2"]
        assert result.reason == "similarity"

    def test_to_dict(self) -> None:
        """to_dict returns correct dictionary."""
        entry = ConsolidatedEntry(
            key="test-consolidated",
            value="Test value",
            source_ids=["src-1", "src-2"],
        )
        result = ConsolidationResult(
            triggered=True,
            consolidated_entry=entry,
            source_keys=["src-1", "src-2"],
            reason="same_topic",
        )
        d = result.to_dict()
        assert d["triggered"] is True
        assert d["consolidated_key"] == "test-consolidated"
        assert d["source_keys"] == ["src-1", "src-2"]
        assert d["reason"] == "same_topic"

    def test_to_dict_no_entry(self) -> None:
        """to_dict handles None consolidated_entry."""
        result = ConsolidationResult(triggered=False)
        d = result.to_dict()
        assert d["consolidated_key"] is None


# ---------------------------------------------------------------------------
# PeriodicScanResult tests
# ---------------------------------------------------------------------------


class TestPeriodicScanResult:
    """Tests for PeriodicScanResult class."""

    def test_default_values(self) -> None:
        """Default values are correctly initialized."""
        result = PeriodicScanResult()
        assert result.scanned is False
        assert result.groups_found == 0
        assert result.entries_consolidated == 0
        assert result.consolidated_entries == []
        assert result.skipped_reason == ""

    def test_with_values(self) -> None:
        """Values are correctly stored."""
        result = PeriodicScanResult(
            scanned=True,
            groups_found=2,
            entries_consolidated=5,
            consolidated_entries=["cons-1", "cons-2"],
        )
        assert result.scanned is True
        assert result.groups_found == 2
        assert result.entries_consolidated == 5
        assert result.consolidated_entries == ["cons-1", "cons-2"]

    def test_to_dict(self) -> None:
        """to_dict returns correct dictionary."""
        result = PeriodicScanResult(
            scanned=True,
            groups_found=2,
            entries_consolidated=5,
            consolidated_entries=["cons-1"],
            skipped_reason="",
        )
        d = result.to_dict()
        assert d["scanned"] is True
        assert d["groups_found"] == 2
        assert d["entries_consolidated"] == 5
        assert d["consolidated_entries"] == ["cons-1"]


# ---------------------------------------------------------------------------
# check_consolidation_on_save tests
# ---------------------------------------------------------------------------


class TestCheckConsolidationOnSave:
    """Tests for check_consolidation_on_save function."""

    def test_not_enough_candidates(
        self, mock_store: MemoryStore, jwt_entries: list[MemoryEntry]
    ) -> None:
        """Returns not triggered when not enough candidates."""
        mock_store.save(
            key=jwt_entries[0].key,
            value=jwt_entries[0].value,
            skip_consolidation=True,
        )
        new_entry = _make_entry(
            "auth-jwt-new",
            "New JWT configuration",
            tags=["security", "jwt"],
        )
        result = check_consolidation_on_save(
            new_entry, mock_store, threshold=0.3, min_entries=5
        )
        assert result.triggered is False
        assert result.reason == "not_enough_candidates"

    def test_no_similar_entries(
        self, mock_store: MemoryStore
    ) -> None:
        """Returns not triggered when no similar entries found."""
        mock_store.save(
            key="unrelated-entry",
            value="Completely unrelated database configuration.",
            tags=["database", "postgres"],
            skip_consolidation=True,
        )
        mock_store.save(
            key="another-unrelated",
            value="Another unrelated entry about testing.",
            tags=["testing", "pytest"],
            skip_consolidation=True,
        )
        new_entry = _make_entry(
            "auth-jwt-new",
            "JWT authentication configuration",
            tags=["security", "jwt"],
        )
        result = check_consolidation_on_save(
            new_entry, mock_store, threshold=0.9, min_entries=2
        )
        assert result.triggered is False
        assert result.reason == "no_similar_entries"

    def test_triggers_consolidation(
        self, mock_store: MemoryStore, jwt_entries: list[MemoryEntry]
    ) -> None:
        """Triggers consolidation when similar entries exist."""
        for entry in jwt_entries:
            mock_store.save(
                key=entry.key,
                value=entry.value,
                tier=entry.tier.value,
                tags=entry.tags,
                skip_consolidation=True,
            )
        new_entry = _make_entry(
            "auth-jwt-new",
            "JWT tokens use RS256 algorithm for security.",
            tier=MemoryTier.architectural,
            tags=["security", "jwt"],
        )
        result = check_consolidation_on_save(
            new_entry, mock_store, threshold=0.3, min_entries=2
        )
        assert result.triggered is True
        assert result.consolidated_entry is not None
        assert len(result.source_keys) >= 2

    def test_min_entries_enforcement(
        self, mock_store: MemoryStore
    ) -> None:
        """min_entries is enforced (at least 2)."""
        mock_store.save(
            key="entry-1",
            value="First entry content about databases",
            tier="pattern",
            tags=["database"],
            skip_consolidation=True,
        )
        new_entry = _make_entry(
            "entry-2",
            "Second entry content about testing",
            tier=MemoryTier.context,
            tags=["testing"],
        )
        result = check_consolidation_on_save(
            new_entry, mock_store, threshold=0.1, min_entries=5
        )
        assert result.triggered is False
        assert result.reason == "not_enough_candidates"

    def test_threshold_behavior(
        self, mock_store: MemoryStore
    ) -> None:
        """High threshold prevents consolidation when entries are not similar enough."""
        mock_store.save(
            key="entry-database",
            value="Use PostgreSQL for data storage with connection pooling.",
            tier="pattern",
            tags=["database", "postgres"],
            skip_consolidation=True,
        )
        mock_store.save(
            key="entry-testing",
            value="Write unit tests with pytest for all code changes.",
            tier="context",
            tags=["testing", "pytest"],
            skip_consolidation=True,
        )
        mock_store.save(
            key="entry-logging",
            value="Use structlog for all application logging needs.",
            tier="pattern",
            tags=["logging", "structlog"],
            skip_consolidation=True,
        )
        new_entry = _make_entry(
            "entry-api",
            "Build REST API with FastAPI framework.",
            tier=MemoryTier.architectural,
            tags=["api", "fastapi"],
        )
        result = check_consolidation_on_save(
            new_entry, mock_store, threshold=0.99, min_entries=2
        )
        assert result.triggered is False
        assert result.reason == "no_similar_entries"


# ---------------------------------------------------------------------------
# run_periodic_consolidation_scan tests
# ---------------------------------------------------------------------------


class TestRunPeriodicConsolidationScan:
    """Tests for run_periodic_consolidation_scan function."""

    def test_skips_when_recently_scanned(
        self, mock_store: MemoryStore, temp_project_root: Path
    ) -> None:
        """Skips scan when last scan was recent."""
        _update_last_scan_time(temp_project_root)
        result = run_periodic_consolidation_scan(
            mock_store,
            temp_project_root,
            scan_interval_days=7,
        )
        assert result.scanned is False
        assert "last_scan" in result.skipped_reason

    def test_scans_when_due(
        self, mock_store: MemoryStore, temp_project_root: Path
    ) -> None:
        """Scans when enough time has passed."""
        state_file = temp_project_root / CONSOLIDATION_STATE_FILE
        state_file.parent.mkdir(parents=True, exist_ok=True)
        old_time = (datetime.now(tz=UTC) - timedelta(days=10)).isoformat()
        state_file.write_text(json.dumps({"last_scan": old_time}))

        result = run_periodic_consolidation_scan(
            mock_store,
            temp_project_root,
            scan_interval_days=7,
        )
        assert result.scanned is True

    def test_force_ignores_last_scan(
        self, mock_store: MemoryStore, temp_project_root: Path
    ) -> None:
        """Force flag ignores last scan time."""
        _update_last_scan_time(temp_project_root)
        result = run_periodic_consolidation_scan(
            mock_store,
            temp_project_root,
            force=True,
        )
        assert result.scanned is True

    def test_no_groups_found(
        self, mock_store: MemoryStore, temp_project_root: Path
    ) -> None:
        """Returns zero groups when none found."""
        mock_store.save(
            key="entry-1",
            value="Unique content here.",
            tags=["unique"],
            skip_consolidation=True,
        )
        mock_store.save(
            key="entry-2",
            value="Completely different content.",
            tags=["different"],
            skip_consolidation=True,
        )
        mock_store.save(
            key="entry-3",
            value="Third unrelated entry.",
            tags=["other"],
            skip_consolidation=True,
        )
        result = run_periodic_consolidation_scan(
            mock_store,
            temp_project_root,
            threshold=0.9,
            force=True,
        )
        assert result.scanned is True
        assert result.groups_found == 0

    def test_consolidates_groups(
        self, mock_store: MemoryStore, temp_project_root: Path, jwt_entries: list[MemoryEntry]
    ) -> None:
        """Consolidates found groups."""
        for entry in jwt_entries:
            mock_store.save(
                key=entry.key,
                value=entry.value,
                tier=entry.tier.value,
                tags=entry.tags,
                skip_consolidation=True,
            )
        result = run_periodic_consolidation_scan(
            mock_store,
            temp_project_root,
            threshold=0.3,
            min_group_size=2,
            force=True,
        )
        assert result.scanned is True

    def test_not_enough_active_entries(
        self, mock_store: MemoryStore, temp_project_root: Path
    ) -> None:
        """Returns early when not enough active entries."""
        mock_store.save(
            key="single-entry",
            value="Only one entry.",
            skip_consolidation=True,
        )
        result = run_periodic_consolidation_scan(
            mock_store,
            temp_project_root,
            min_group_size=3,
            force=True,
        )
        assert result.scanned is True
        assert result.skipped_reason == "not_enough_active_entries"


# ---------------------------------------------------------------------------
# State file helper tests
# ---------------------------------------------------------------------------


class TestStateFileHelpers:
    """Tests for state file helper functions."""

    def test_get_last_scan_time_no_file(self, temp_project_root: Path) -> None:
        """Returns None when no state file exists."""
        result = _get_last_scan_time(temp_project_root)
        assert result is None

    def test_get_last_scan_time_with_file(self, temp_project_root: Path) -> None:
        """Returns datetime when state file exists."""
        state_file = temp_project_root / CONSOLIDATION_STATE_FILE
        state_file.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(tz=UTC)
        state_file.write_text(json.dumps({"last_scan": now.isoformat()}))

        result = _get_last_scan_time(temp_project_root)
        assert result is not None
        assert (now - result).total_seconds() < 1

    def test_get_last_scan_time_invalid_json(self, temp_project_root: Path) -> None:
        """Returns None for invalid JSON."""
        state_file = temp_project_root / CONSOLIDATION_STATE_FILE
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("invalid json")

        result = _get_last_scan_time(temp_project_root)
        assert result is None

    def test_update_last_scan_time_creates_file(self, temp_project_root: Path) -> None:
        """Creates state file if it doesn't exist."""
        _update_last_scan_time(temp_project_root)

        state_file = temp_project_root / CONSOLIDATION_STATE_FILE
        assert state_file.exists()

        data = json.loads(state_file.read_text())
        assert "last_scan" in data

    def test_update_last_scan_time_preserves_other_data(
        self, temp_project_root: Path
    ) -> None:
        """Preserves other data in state file."""
        state_file = temp_project_root / CONSOLIDATION_STATE_FILE
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"other_key": "other_value"}))

        _update_last_scan_time(temp_project_root)

        data = json.loads(state_file.read_text())
        assert data["other_key"] == "other_value"
        assert "last_scan" in data


# ---------------------------------------------------------------------------
# should_run_auto_consolidation tests
# ---------------------------------------------------------------------------


class TestShouldRunAutoConsolidation:
    """Tests for should_run_auto_consolidation helper."""

    def test_returns_true_when_enabled(self, temp_project_root: Path) -> None:
        """Returns True when auto_consolidate is True."""
        result = should_run_auto_consolidation(
            temp_project_root,
            auto_consolidate=True,
        )
        assert result is True

    def test_returns_false_when_disabled(self, temp_project_root: Path) -> None:
        """Returns False when auto_consolidate is False."""
        result = should_run_auto_consolidation(
            temp_project_root,
            auto_consolidate=False,
        )
        assert result is False


# ---------------------------------------------------------------------------
# MemoryStore integration tests
# ---------------------------------------------------------------------------


class TestMemoryStoreConsolidation:
    """Integration tests for MemoryStore with auto-consolidation."""

    def test_consolidation_disabled_by_default(self, temp_project_root: Path) -> None:
        """Consolidation is disabled by default."""
        store = MemoryStore(temp_project_root)
        try:
            assert store._consolidation_config.enabled is False
        finally:
            store.close()

    def test_consolidation_enabled_via_config(self, temp_project_root: Path) -> None:
        """Consolidation can be enabled via config."""
        config = ConsolidationConfig(enabled=True, threshold=0.7, min_entries=3)
        store = MemoryStore(temp_project_root, consolidation_config=config)
        try:
            assert store._consolidation_config.enabled is True
        finally:
            store.close()

    def test_skip_consolidation_flag(self, temp_project_root: Path) -> None:
        """skip_consolidation flag prevents consolidation check."""
        config = ConsolidationConfig(enabled=True, threshold=0.1, min_entries=2)
        store = MemoryStore(temp_project_root, consolidation_config=config)
        try:
            store.save(
                key="entry-1",
                value="First entry content",
                skip_consolidation=True,
            )
            store.save(
                key="entry-2",
                value="First entry similar content",
                skip_consolidation=True,
            )
            store.save(
                key="entry-3",
                value="First entry almost same",
                skip_consolidation=True,
            )

            assert store.count() == 3
        finally:
            store.close()

    def test_set_consolidation_config(self, temp_project_root: Path) -> None:
        """Consolidation config can be updated."""
        store = MemoryStore(temp_project_root)
        try:
            assert store._consolidation_config.enabled is False

            new_config = ConsolidationConfig(enabled=True)
            store.set_consolidation_config(new_config)
            assert store._consolidation_config.enabled is True
        finally:
            store.close()

    def test_project_root_property(self, temp_project_root: Path) -> None:
        """project_root property returns correct path."""
        store = MemoryStore(temp_project_root)
        try:
            assert store.project_root == temp_project_root
        finally:
            store.close()
