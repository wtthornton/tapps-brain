"""Minimal feature flags for optional dependencies.

Detects optional packages once (lazily on first access) and caches the
results. Only includes flags for legitimately optional LLM-as-judge deps.
"""

from __future__ import annotations

import importlib.util


class FeatureFlags:
    """Lazy-evaluated feature flags for optional dependencies."""

    def __init__(self) -> None:
        self._cache: dict[str, bool] = {}

    @staticmethod
    def _probe(module_name: str) -> bool:
        """Return whether *module_name* is importable."""
        try:
            return importlib.util.find_spec(module_name) is not None
        except (ModuleNotFoundError, ValueError):
            return False

    @property
    def anthropic_sdk(self) -> bool:
        """True when ``anthropic`` is importable (LLM-as-judge, EPIC-031)."""
        if "anthropic_sdk" not in self._cache:
            self._cache["anthropic_sdk"] = self._probe("anthropic")
        return self._cache["anthropic_sdk"]

    @property
    def openai_sdk(self) -> bool:
        """True when ``openai`` is importable (LLM-as-judge, EPIC-031)."""
        if "openai_sdk" not in self._cache:
            self._cache["openai_sdk"] = self._probe("openai")
        return self._cache["openai_sdk"]

    def reset(self) -> None:
        """Clear the cached detection results (for test isolation)."""
        self._cache.clear()

    def as_dict(self) -> dict[str, bool]:
        """Return all flags as a plain dict."""
        _ = (self.anthropic_sdk, self.openai_sdk)
        return dict(self._cache)


#: Module-level singleton.
feature_flags = FeatureFlags()
