"""Unit coverage for the TAP-6753 dry-run remediation script.

The script (``scripts/remediate_out_of_enum_tiers.py``) never mutates
``private_memories`` — these tests exercise its pure helpers only, without a
database connection.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "remediate_out_of_enum_tiers.py"
_spec = importlib.util.spec_from_file_location("remediate_out_of_enum_tiers", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)


def test_backup_table_name_is_deterministic_and_namespaced() -> None:
    now = datetime(2026, 9, 14, 20, 30, 0, tzinfo=UTC)
    name = _module._backup_table_name(now)
    assert name == "private_memories_tier_backfill_backup_20260914_203000"


def test_backup_table_name_changes_with_the_timestamp() -> None:
    first = _module._backup_table_name(datetime(2026, 9, 14, 0, 0, 0, tzinfo=UTC))
    second = _module._backup_table_name(datetime(2026, 9, 15, 0, 0, 0, tzinfo=UTC))
    assert first != second


def test_module_defines_no_apply_path() -> None:
    """The script must never grow a mutating code path (dry-run only, TAP-6753)."""
    source = _SCRIPT_PATH.read_text()
    for banned in ('cur.execute("UPDATE', 'cur.execute("INSERT', 'cur.execute("DELETE'):
        assert banned not in source, f"found a mutating statement: {banned!r}"


def test_enum_tiers_constant_matches_the_model() -> None:
    from tapps_brain.models import MemoryTier

    assert _module._ENUM_TIERS == tuple(t.value for t in MemoryTier)
