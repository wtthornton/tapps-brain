"""Tests for tier normalization (GitHub #48)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import NoReturn

import pytest

import tapps_brain.tier_normalize as tier_normalize_mod
from tapps_brain.models import MemoryTier
from tapps_brain.tier_normalize import normalize_save_tier


class _Profile:
    """Minimal stand-in for MemoryProfile.layer_names matching."""

    def __init__(self, names: list[str]) -> None:
        self.layer_names = names


def test_empty_defaults_to_pattern() -> None:
    assert normalize_save_tier(None, None) == MemoryTier.pattern.value
    assert normalize_save_tier("  ", None) == MemoryTier.pattern.value


def test_profile_layer_case_insensitive() -> None:
    p = _Profile(["identity", "long-term", "short-term"])
    assert normalize_save_tier("IDENTITY", p) == "identity"
    assert normalize_save_tier("Long-Term", p) == "long-term"


def test_profile_wins_over_global_alias() -> None:
    """``long-term`` is a profile layer name, not architectural, when profile defines it."""
    p = _Profile(["long-term"])
    assert normalize_save_tier("long-term", p) == "long-term"


def test_long_term_alias_without_profile() -> None:
    assert normalize_save_tier("long-term", None) == MemoryTier.architectural.value


def test_unknown_maps_to_pattern() -> None:
    assert normalize_save_tier("totally-made-up-tier", None) == MemoryTier.pattern.value


def test_enum_case_insensitive() -> None:
    assert normalize_save_tier("ARCHITECTURAL", None) == MemoryTier.architectural.value


def test_member_value_loop_when_constructor_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover fallback loop when ``MemoryTier(t)`` fails but ``t`` matches a member value."""

    class _CallableShim:
        pattern = MemoryTier

        def __call__(self, _val: str) -> NoReturn:
            raise ValueError("forced")

        def __iter__(self) -> Iterator[MemoryTier]:
            return iter(MemoryTier)

    monkeypatch.setattr(tier_normalize_mod, "MemoryTier", _CallableShim())
    assert normalize_save_tier("procedural", None) == MemoryTier.procedural.value
