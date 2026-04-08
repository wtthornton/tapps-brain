"""sqlite_vec import/load edge cases (no module-level importorskip)."""

from __future__ import annotations

import builtins
import sqlite3

import pytest


def test_load_extension_raises_on_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def guarded(
        name: str,
        globals_arg: dict[str, object] | None = None,
        locals_arg: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "sqlite_vec":
            raise ImportError("sqlite_vec not installed")
        return real_import(name, globals_arg, locals_arg, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)
    from tapps_brain.sqlite_vec_index import load_extension

    conn = sqlite3.connect(":memory:")
    with pytest.raises(ImportError, match="sqlite_vec not installed"):
        load_extension(conn)
    conn.close()
