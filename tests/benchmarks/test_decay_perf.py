"""Decay model performance benchmark (STORY-SC02 / TAP-558).

Measures CPU cost of power-law vs exponential decay at 10k-memory scale.
The story AC requires power-law to add < 5 % CPU vs exponential; verify by
comparing the mean of ``test_exponential_baseline`` and
``test_power_law_repo_brain_profile`` in the pytest-benchmark report.

Run with::

    pytest tests/benchmarks/test_decay_perf.py -v --benchmark-only
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from tapps_brain.decay import (
    DecayConfig,
    calculate_decayed_confidence,
    exponential_decay,
    power_law_decay,
)
from tests.factories import make_entry

if TYPE_CHECKING:
    from tapps_brain.models import MemoryEntry

pytestmark = pytest.mark.benchmark


_N = 10_000
_TIERS = ("architectural", "pattern", "procedural", "context")


def _make_entries(n: int = _N) -> list[MemoryEntry]:
    now = datetime.now(tz=UTC)
    entries: list[MemoryEntry] = []
    for i in range(n):
        age_days = (i % 365) + 1
        tier = _TIERS[i % len(_TIERS)]
        updated = (now - timedelta(days=age_days)).isoformat()
        entries.append(
            make_entry(
                key=f"decay-bench-{i}",
                value=f"entry {i}",
                tier=tier,
                confidence=0.5 + (i % 50) / 100.0,
                updated_at=updated,
            )
        )
    return entries


def _power_law_config() -> DecayConfig:
    """All four tiers on power-law with the half-life-anchor β ≈ 3.29."""
    return DecayConfig(
        decay_model="power_law",
        decay_exponent=3.29,
        layer_decay_models=dict.fromkeys(_TIERS, "power_law"),
        layer_decay_exponents=dict.fromkeys(_TIERS, 3.29),
    )


def _exponential_config() -> DecayConfig:
    return DecayConfig()  # default is exponential


class TestDecayModelPerf:
    """Compare power-law vs exponential CPU cost at 10k-memory scale."""

    def test_exponential_baseline(self, benchmark) -> None:
        """Baseline: exponential decay over 10k entries."""
        config = _exponential_config()
        entries = _make_entries()
        now = datetime.now(tz=UTC)

        def run() -> None:
            for e in entries:
                calculate_decayed_confidence(e, config, now=now)

        benchmark(run)

    def test_power_law_repo_brain_profile(self, benchmark) -> None:
        """Power-law with repo-brain's half-life-anchor β across all tiers."""
        config = _power_law_config()
        entries = _make_entries()
        now = datetime.now(tz=UTC)

        def run() -> None:
            for e in entries:
                calculate_decayed_confidence(e, config, now=now)

        benchmark(run)


class TestDecayPrimitives:
    """Micro-benchmark the raw numeric primitives (no config lookup overhead)."""

    def test_power_law_decay_primitive(self, benchmark) -> None:
        def run() -> None:
            for t in range(_N):
                power_law_decay(float(t % 365 + 1), 30.0, 3.29)

        benchmark(run)

    def test_exponential_decay_primitive(self, benchmark) -> None:
        def run() -> None:
            for t in range(_N):
                exponential_decay(float(t % 365 + 1), 30.0)

        benchmark(run)
