"""Regression tests for auto_consolidation thread-safety (TAP-1808).

Verifies that ``undo_consolidation_merge`` holds ``store._lock`` (via
``store._serialized()``) for the entire restore-and-cleanup sequence so
that concurrent ``save()`` calls do not observe a partially-restored state
and no write is silently lost.
"""

from __future__ import annotations

import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from tapps_brain.auto_consolidation import (
    ConsolidationUndoResult,
    check_consolidation_on_save,
)
from tapps_brain.models import MemoryTier
from tapps_brain.store import MemoryStore
from tests.factories import make_entry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def temp_root() -> Path:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        yield Path(tmp)


@pytest.fixture()
def store(temp_root: Path) -> Iterator[MemoryStore]:
    s = MemoryStore(temp_root)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_consolidated_entry(store: MemoryStore) -> str:
    """Save two similar JWT entries, consolidate them, and return the consolidated key.

    Uses a low similarity threshold (0.3) with JWT-domain content that the
    existing test suite already exercises at this threshold — so the
    consolidation is guaranteed to trigger on any platform.
    """
    e1 = make_entry(
        "auth-jwt-algorithm",
        "Use RS256 for JWT signing. Store keys in environment variables.",
        tier=MemoryTier.architectural,
        tags=["security", "jwt"],
    )
    e2 = make_entry(
        "auth-jwt-token-signing",
        "JWT tokens must use RS256 algorithm. Private key lives in env vars.",
        tier=MemoryTier.architectural,
        tags=["security", "jwt"],
    )
    for e in (e1, e2):
        store.save(
            key=e.key,
            value=e.value,
            tier=e.tier.value,
            tags=e.tags,
            skip_consolidation=True,
        )
    result = check_consolidation_on_save(e2, store, threshold=0.3, min_entries=2)
    if not result.triggered or result.consolidated_entry is None:
        pytest.fail(
            "Consolidation did not trigger with threshold=0.3 and JWT-domain content. "
            "This is a setup error, not a platform variance — check similarity scoring."
        )
    return result.consolidated_entry.key


# ---------------------------------------------------------------------------
# Thread-safety regression (TAP-1808)
# ---------------------------------------------------------------------------


class TestUndoConsolidationMergeLockSafety:
    """Regression for TAP-1808.

    ``undo_consolidation_merge`` must hold ``store._lock`` for the full
    in-memory restore-and-cleanup sequence.  A partial undo observable by a
    concurrent reader would surface as a ``KeyError`` on the next
    ``recall()``; a race with a concurrent ``save()`` could silently drop a
    write.
    """

    def test_no_key_error_and_no_lost_write_under_concurrent_saves(
        self, store: MemoryStore
    ) -> None:
        """8 concurrent save()s racing against undo_consolidation_merge.

        Acceptance criteria (TAP-1808):
        - No ``KeyError`` raised by any thread.
        - Every concurrent ``save()`` entry is retrievable after all threads
          complete (no lost write).
        - The undo thread produces exactly one ``ConsolidationUndoResult``
          that is either ``ok=True`` or ``reason="consolidated_entry_missing"``
          (the latter when a saver happened to delete/move the key first,
          which is not possible here, but guarded for completeness).
        """
        consolidated_key = _setup_consolidated_entry(store)

        n_savers = 8
        thread_errors: list[Exception] = []
        errors_lock = threading.Lock()
        undo_results: list[ConsolidationUndoResult] = []

        def _saver(idx: int) -> None:
            try:
                store.save(
                    key=f"concurrent-saver-{idx}",
                    value=f"concurrent value from thread {idx}",
                    skip_consolidation=True,
                )
            except Exception as exc:  # noqa: BLE001
                with errors_lock:
                    thread_errors.append(exc)

        def _undo() -> None:
            try:
                result = store.undo_consolidation_merge(consolidated_key)
                undo_results.append(result)
            except Exception as exc:  # noqa: BLE001
                with errors_lock:
                    thread_errors.append(exc)

        threads: list[threading.Thread] = [
            threading.Thread(target=_saver, args=(i,), name=f"saver-{i}")
            for i in range(n_savers)
        ]
        threads.append(threading.Thread(target=_undo, name="undo"))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # ----- assert no thread crashed -----
        key_errors = [e for e in thread_errors if isinstance(e, KeyError)]
        assert key_errors == [], (
            f"KeyError(s) during concurrent undo/save race: {key_errors}  "
            "This means undo_consolidation_merge exposed a partial state while "
            "not holding store._lock."
        )
        assert thread_errors == [], f"Unexpected exceptions from threads: {thread_errors}"

        # ----- assert no lost write -----
        for i in range(n_savers):
            saved = store.get(f"concurrent-saver-{i}")
            assert saved is not None, (
                f"Write from saver thread {i} was lost — "
                "a concurrent undo mutated _entries without holding the lock."
            )

        # ----- assert undo produced a coherent result -----
        assert len(undo_results) == 1, (
            "undo thread did not produce a ConsolidationUndoResult"
        )
        r = undo_results[0]
        assert r.ok is True, (
            f"undo_consolidation_merge failed with reason: {r.reason!r} — "
            "concurrent saves write only unique keys and cannot remove the consolidated entry, "
            "so undo must succeed."
        )
