"""Encryption key wiring to ``connect_sqlite`` (GitHub #23)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tapps_brain.persistence import MemoryPersistence
from tapps_brain.sqlcipher_util import resolve_sqlite_busy_timeout_ms


def _connect_shim(captured: list[str | None]):
    def shim(
        path: object,
        *,
        encryption_key: str | None,
        check_same_thread: bool = False,
    ) -> sqlite3.Connection:
        captured.append(encryption_key)
        conn = sqlite3.connect(str(path), check_same_thread=check_same_thread)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        busy_ms = resolve_sqlite_busy_timeout_ms()
        conn.execute(f"PRAGMA busy_timeout={busy_ms}")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    return shim


def test_memory_persistence_forwards_encryption_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str | None] = []
    monkeypatch.setattr(
        "tapps_brain.persistence.connect_sqlite",
        _connect_shim(captured),
    )
    mp = MemoryPersistence(tmp_path, encryption_key="unit-secret")
    try:
        assert captured == ["unit-secret"]
        assert mp.encryption_key == "unit-secret"
        assert mp.sqlcipher_enabled is True
    finally:
        mp.close()


def test_feedback_store_forwards_encryption_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    MemoryPersistence(tmp_path).close()
    db = tmp_path / ".tapps-brain" / "memory" / "memory.db"
    captured: list[str | None] = []
    monkeypatch.setattr(
        "tapps_brain.feedback.connect_sqlite",
        _connect_shim(captured),
    )
    from tapps_brain.feedback import FeedbackStore

    fs = FeedbackStore(db_path=db, encryption_key="fbk")
    try:
        assert captured == ["fbk"]
    finally:
        fs.close()


def test_diagnostics_history_forwards_encryption_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    MemoryPersistence(tmp_path).close()
    db = tmp_path / ".tapps-brain" / "memory" / "memory.db"
    captured: list[str | None] = []
    monkeypatch.setattr(
        "tapps_brain.diagnostics.connect_sqlite",
        _connect_shim(captured),
    )
    from tapps_brain.diagnostics import DiagnosticsHistoryStore

    hs = DiagnosticsHistoryStore(db, encryption_key="dk")
    try:
        assert captured == ["dk"]
    finally:
        hs.close()


def test_memory_store_passes_encryption_key_to_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str | None] = []
    monkeypatch.setattr(
        "tapps_brain.persistence.connect_sqlite",
        _connect_shim(captured),
    )
    from tapps_brain.store import MemoryStore

    store = MemoryStore(tmp_path, encryption_key="skey")
    try:
        assert captured[0] == "skey"
        assert store.health().sqlcipher_enabled is True
    finally:
        store.close()


def test_hive_store_forwards_encryption_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str | None] = []
    monkeypatch.setattr(
        "tapps_brain.hive.connect_sqlite",
        _connect_shim(captured),
    )
    from tapps_brain.hive import HiveStore

    h = HiveStore(db_path=tmp_path / "hive.db", encryption_key="hive-k")
    try:
        assert captured == ["hive-k"]
    finally:
        h.close()
