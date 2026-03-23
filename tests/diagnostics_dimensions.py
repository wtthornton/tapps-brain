"""Test-only HealthDimension factories for diagnostics (EPIC-030)."""

from __future__ import annotations

from typing import Any

from tapps_brain.diagnostics import DimensionScore


class AlwaysGoodDimension:
    """Returns a perfect score for any store."""

    @property
    def name(self) -> str:
        return "always_good"

    @property
    def default_weight(self) -> float:
        return 0.1

    def check(self, store: Any) -> DimensionScore:
        _ = store
        return DimensionScore(name=self.name, score=1.0, raw_details={"stub": True})


def make_always_good() -> AlwaysGoodDimension:
    return AlwaysGoodDimension()
