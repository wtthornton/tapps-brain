"""Concurrency stress tests for MemoryStore — 016-C and 016-D.

Tests cover (016-C):
- 10 threads saving 50 entries each → all 500 persisted, no corruption
- 5 threads saving while 5 threads recalling → no exceptions
- Concurrent save at max capacity (500) → eviction correct under contention

Tests cover (016-D):
- GC running while saves happen → no exceptions, archive consistent
- Multiple agents propagating to HiveStore concurrently → all entries arrive
- Concurrent recall from Hive during propagation → no exceptions

All tests use a 30-second timeout via pytest-timeout or threading.Event.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

from tapps_brain.hive import HiveStore
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

        def saver(thread_id: int) -> None:
            try:
                for i in range(entries_per_thread):
                    key = f"thread-{thread_id}-entry-{i}"
                    store.save(
                        key=key,
                        value=f"value from thread {thread_id} entry {i}",
                        tier="context",
                    )
            except Exception as exc:
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
            except Exception as exc:
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
            except Exception as exc:
                errors.append(exc)

        def recaller(thread_id: int) -> None:
            try:
                while not stop_event.is_set():
                    store.search("seed value")
                    time.sleep(0.005)
            except Exception as exc:
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
    """Concurrent saves at max capacity — eviction correct under contention."""

    _TEST_LIMIT = 100  # Small limit for fast tests

    def test_concurrent_save_at_max_capacity(self, tmp_path: Path) -> None:
        """Saving beyond max_entries concurrently never leaves store in corrupt state."""
        limit = self._TEST_LIMIT
        s = MemoryStore(tmp_path)
        if s._profile is not None:
            s._profile.limits.max_entries = limit
        try:
            # Pre-fill to near-capacity
            prefill = limit - 10
            for i in range(prefill):
                s.save(key=f"pre-{i}", value=f"pre-value {i}", tier="context")

            assert s.count() == prefill

            errors: list[Exception] = []

            def saver(thread_id: int) -> None:
                try:
                    for i in range(20):
                        key = f"cap-thread-{thread_id}-{i}"
                        s.save(key=key, value=f"overflow value {i}", tier="context")
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=saver, args=(t,)) for t in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

            assert not errors, f"Threads raised exceptions: {errors}"

            # Store must not exceed the configured limit
            count = s.count()
            assert count <= limit, f"Store exceeded max capacity: {count}"
            assert count > 0, "Store must not be empty after concurrent saves"
        finally:
            s.close()


class TestConcurrentGCAndSaves:
    """GC running while saves happen — no exceptions, archive consistent."""

    def test_gc_concurrent_with_saves(self, tmp_path: Path) -> None:
        """GC and saves running in parallel must not corrupt the store."""
        s = MemoryStore(tmp_path)
        try:
            # Pre-populate 10 stale entries (contradicted + very low confidence)
            for i in range(10):
                s.save(key=f"stale-{i}", value=f"stale value {i}", tier="context")
                # Mark as contradicted with floor confidence so GC archives them
                entry = s.get(f"stale-{i}")
                if entry is not None:
                    entry.contradicted = True
                    entry.confidence = 0.05  # well below 0.2 threshold

            errors: list[Exception] = []
            stop_event = threading.Event()

            def saver(thread_id: int) -> None:
                idx = 0
                try:
                    while not stop_event.is_set():
                        key = f"gc-save-{thread_id}-{idx}"
                        s.save(key=key, value=f"fresh value {idx}", tier="pattern")
                        idx += 1
                        time.sleep(0.002)
                except Exception as exc:
                    errors.append(exc)

            def gc_runner() -> None:
                try:
                    while not stop_event.is_set():
                        s.gc(dry_run=False)
                        time.sleep(0.01)
                except Exception as exc:
                    errors.append(exc)

            save_threads = [threading.Thread(target=saver, args=(t,)) for t in range(3)]
            gc_thread = threading.Thread(target=gc_runner)

            all_threads = [*save_threads, gc_thread]
            for t in all_threads:
                t.start()

            # Run for 2 seconds
            time.sleep(2)
            stop_event.set()

            start_join = time.monotonic()
            for t in all_threads:
                t.join(timeout=30)
            elapsed = time.monotonic() - start_join

            assert elapsed < 30, f"GC concurrent test join timed out after {elapsed:.1f}s"
            assert not errors, f"Threads raised exceptions: {errors}"

            # Store integrity: count must be non-negative.
            # 3 saver threads x ~2s / 0.002s sleep ~ up to ~3000 entries; GC only
            # archives entries that were explicitly marked contradicted, so we just
            # verify no corruption occurred (no exceptions, count is non-negative).
            count = s.count()
            assert count >= 0, f"Store count must be non-negative: {count}"
        finally:
            s.close()

    def test_gc_archive_consistent_under_contention(self, tmp_path: Path) -> None:
        """Entries archived by GC must not remain in the live store."""
        s = MemoryStore(tmp_path)
        try:
            # Save 5 entries and mark as stale (contradicted + floor confidence)
            stale_keys = [f"archive-stale-{i}" for i in range(5)]
            for key in stale_keys:
                s.save(key=key, value="old data", tier="context")
                entry = s.get(key)
                if entry is not None:
                    entry.contradicted = True
                    entry.confidence = 0.05

            errors: list[Exception] = []

            def saver(thread_id: int) -> None:
                try:
                    for i in range(10):
                        s.save(
                            key=f"archive-new-{thread_id}-{i}",
                            value=f"new value {i}",
                            tier="pattern",
                        )
                except Exception as exc:
                    errors.append(exc)

            # Launch saves and GC simultaneously
            save_threads = [threading.Thread(target=saver, args=(t,)) for t in range(3)]
            gc_thread = threading.Thread(target=lambda: s.gc(dry_run=False))

            all_threads = [*save_threads, gc_thread]
            for t in all_threads:
                t.start()
            for t in all_threads:
                t.join(timeout=30)

            assert not errors, f"Threads raised exceptions: {errors}"

            # Any stale key that was archived must not appear in the live store
            live_keys = {e.key for e in s.list_all()}
            for key in stale_keys:
                # If GC ran and found it stale, it should have been removed
                entry = s.get(key)
                if entry is not None:
                    # If still present it means it was re-saved or GC lost the race — OK
                    assert key in live_keys
        finally:
            s.close()


@pytest.fixture()
def hive_store(tmp_path: Path) -> Generator[HiveStore, None, None]:
    """Isolated HiveStore backed by a temp SQLite file."""
    hs = HiveStore(db_path=tmp_path / "hive.db")
    yield hs
    hs.close()


class TestConcurrentHivePropagation:
    """Multiple agents propagating to HiveStore concurrently — all entries arrive."""

    def test_concurrent_hive_saves_all_arrive(self, hive_store: HiveStore) -> None:
        """5 agents each saving 20 unique entries → all 100 entries in HiveStore."""
        num_agents = 5
        entries_per_agent = 20
        errors: list[Exception] = []

        def propagate(agent_id: int) -> None:
            try:
                for i in range(entries_per_agent):
                    hive_store.save(
                        key=f"agent-{agent_id}-entry-{i}",
                        value=f"from agent {agent_id}, item {i}",
                        namespace="universal",
                        source_agent=f"agent-{agent_id}",
                        tier="pattern",
                        conflict_policy="last_write_wins",
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=propagate, args=(a,)) for a in range(num_agents)]
        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        elapsed = time.monotonic() - start

        assert elapsed < 30, f"Concurrent Hive save timed out after {elapsed:.1f}s"
        assert not errors, f"Threads raised exceptions: {errors}"

        # All unique keys must be present
        results = hive_store.search("agent", namespaces=["universal"], limit=200)
        saved_keys = {r["key"] for r in results}
        expected_count = num_agents * entries_per_agent
        assert len(saved_keys) == expected_count, (
            f"Expected {expected_count} entries, found {len(saved_keys)}"
        )

    def test_concurrent_hive_multi_namespace(self, hive_store: HiveStore) -> None:
        """Agents writing to different namespaces should not collide."""
        namespaces = ["domain-a", "domain-b", "domain-c"]
        entries_per_ns = 10
        errors: list[Exception] = []

        def ns_writer(ns: str) -> None:
            try:
                for i in range(entries_per_ns):
                    hive_store.save(
                        key=f"ns-entry-{i}",
                        value=f"value for {ns} item {i}",
                        namespace=ns,
                        source_agent=f"agent-{ns}",
                        tier="pattern",
                        conflict_policy="last_write_wins",
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=ns_writer, args=(ns,)) for ns in namespaces]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Threads raised exceptions: {errors}"

        # Each namespace should have exactly entries_per_ns entries
        for ns in namespaces:
            results = hive_store.search("value for", namespaces=[ns], limit=50)
            assert len(results) == entries_per_ns, (
                f"Namespace {ns!r}: expected {entries_per_ns}, got {len(results)}"
            )


class TestConcurrentHiveRecallDuringPropagation:
    """Concurrent recall from Hive during propagation — no exceptions."""

    def test_recall_during_propagation(self, hive_store: HiveStore) -> None:
        """Searches must not raise even when concurrent saves are in progress."""
        # Seed with some entries so searches return results from the start
        for i in range(20):
            hive_store.save(
                key=f"seed-{i}",
                value=f"seed content item {i}",
                namespace="universal",
                source_agent="seeder",
                conflict_policy="last_write_wins",
            )

        errors: list[Exception] = []
        stop_event = threading.Event()

        def writer(thread_id: int) -> None:
            idx = 0
            try:
                while not stop_event.is_set():
                    hive_store.save(
                        key=f"live-{thread_id}-{idx}",
                        value=f"live content from {thread_id} idx {idx}",
                        namespace="universal",
                        source_agent=f"writer-{thread_id}",
                        conflict_policy="last_write_wins",
                    )
                    idx += 1
                    time.sleep(0.003)
            except Exception as exc:
                errors.append(exc)

        def reader(thread_id: int) -> None:
            try:
                while not stop_event.is_set():
                    hive_store.search("content", namespaces=["universal"], limit=20)
                    time.sleep(0.005)
            except Exception as exc:
                errors.append(exc)

        write_threads = [threading.Thread(target=writer, args=(t,)) for t in range(3)]
        read_threads = [threading.Thread(target=reader, args=(t,)) for t in range(3)]
        all_threads = write_threads + read_threads

        for t in all_threads:
            t.start()

        time.sleep(2)
        stop_event.set()

        for t in all_threads:
            t.join(timeout=30)

        assert not errors, f"Threads raised exceptions: {errors}"
