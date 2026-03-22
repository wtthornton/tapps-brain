"""Concurrency stress tests for MemoryStore — 016-C.

Tests cover:
- 10 threads saving 50 entries each → all 500 persisted, no corruption
- 5 threads saving while 5 threads recalling → no exceptions
- Concurrent save at max capacity (500) → eviction correct under contention

All tests use a 30-second timeout via pytest-timeout or threading.Event.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tapps_brain.store import MemoryStore


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(tmp_path)
    yield s
    s.close()


class TestConcurrentSave:
    """10 threads each saving 50 unique entries → all 500 entries persisted."""

    def test_concurrent_save_all_persisted(self, store: MemoryStore) -> None:
        num_threads = 10
        entries_per_thread = 50
        errors: list[Exception] = []
        done = threading.Event()

        def saver(thread_id: int) -> None:
            try:
                for i in range(entries_per_thread):
                    key = f"thread-{thread_id}-entry-{i}"
                    store.save(
                        key=key,
                        value=f"value from thread {thread_id} entry {i}",
                        tier="context",
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=saver, args=(t,)) for t in range(num_threads)]
        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        elapsed = time.monotonic() - start
        assert elapsed < 30, f"Concurrent save test timed out after {elapsed:.1f}s"
        assert not errors, f"Threads raised exceptions: {errors}"

        # All 500 entries must be present (store enforces max 500)
        count = store.count()
        assert count == num_threads * entries_per_thread

    def test_concurrent_save_no_data_corruption(self, store: MemoryStore) -> None:
        """Values written by threads must be readable without corruption."""
        num_threads = 5
        entries_per_thread = 20
        errors: list[Exception] = []

        def saver(thread_id: int) -> None:
            try:
                for i in range(entries_per_thread):
                    key = f"corruption-thread-{thread_id}-{i}"
                    val = f"thread={thread_id} i={i}"
                    store.save(key=key, value=val, tier="context")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=saver, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Threads raised exceptions: {errors}"

        # Spot-check: all keys should be retrievable with correct values
        for thread_id in range(num_threads):
            for i in range(entries_per_thread):
                key = f"corruption-thread-{thread_id}-{i}"
                entry = store.get(key)
                assert entry is not None, f"Missing entry for {key}"
                assert f"thread={thread_id}" in entry.value, (
                    f"Value corrupted for {key}: {entry.value!r}"
                )


class TestConcurrentSaveAndRecall:
    """5 threads saving while 5 threads recalling — no exceptions."""

    def test_concurrent_save_and_recall(self, store: MemoryStore) -> None:
        # Pre-populate a few entries so recall has something to return
        for i in range(10):
            store.save(key=f"seed-{i}", value=f"seed value {i}", tier="pattern")

        errors: list[Exception] = []
        stop_event = threading.Event()

        def saver(thread_id: int) -> None:
            try:
                idx = 0
                while not stop_event.is_set():
                    key = f"concurrent-save-{thread_id}-{idx}"
                    store.save(key=key, value=f"value {idx}", tier="context")
                    idx += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def recaller(thread_id: int) -> None:
            try:
                while not stop_event.is_set():
                    store.search("seed value")
                    time.sleep(0.005)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        save_threads = [threading.Thread(target=saver, args=(t,)) for t in range(5)]
        recall_threads = [threading.Thread(target=recaller, args=(t,)) for t in range(5)]
        all_threads = save_threads + recall_threads

        for t in all_threads:
            t.start()

        # Let threads run for 2 seconds then stop
        time.sleep(2)
        stop_event.set()

        for t in all_threads:
            t.join(timeout=30)

        assert not errors, f"Threads raised exceptions: {errors}"


class TestConcurrentSaveAtCapacity:
    """Concurrent saves at max capacity (500) — eviction correct under contention."""

    def test_concurrent_save_at_max_capacity(self, tmp_path: Path) -> None:
        """Saving beyond 500 entries concurrently never leaves store in corrupt state."""
        s = MemoryStore(tmp_path)
        try:
            # Pre-fill to 490 entries
            for i in range(490):
                s.save(key=f"pre-{i}", value=f"pre-value {i}", tier="context")

            assert s.count() == 490

            errors: list[Exception] = []

            def saver(thread_id: int) -> None:
                try:
                    for i in range(20):
                        key = f"cap-thread-{thread_id}-{i}"
                        s.save(key=key, value=f"overflow value {i}", tier="context")
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=saver, args=(t,)) for t in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

            assert not errors, f"Threads raised exceptions: {errors}"

            # Store must not exceed 500 entries
            count = s.count()
            assert count <= 500, f"Store exceeded max capacity: {count}"
            assert count > 0, "Store must not be empty after concurrent saves"
        finally:
            s.close()
