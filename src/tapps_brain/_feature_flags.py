"""Minimal feature flags for optional dependencies.

Detects optional packages once (lazily on first access) and caches the
results. Only includes flags relevant to the brain's optional features
(vector search, reranking).
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
    def faiss(self) -> bool:
        """True when ``faiss`` (faiss-cpu) is importable."""
        if "faiss" not in self._cache:
            self._cache["faiss"] = self._probe("faiss")
        return self._cache["faiss"]

    @property
    def numpy(self) -> bool:
        """True when ``numpy`` is importable."""
        if "numpy" not in self._cache:
            self._cache["numpy"] = self._probe("numpy")
        return self._cache["numpy"]

    @property
    def sentence_transformers(self) -> bool:
        """True when ``sentence_transformers`` is importable."""
        if "sentence_transformers" not in self._cache:
            self._cache["sentence_transformers"] = self._probe("sentence_transformers")
        return self._cache["sentence_transformers"]

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

    @property
    def otel(self) -> bool:
        """True when ``opentelemetry`` SDK is importable."""
        if "otel" not in self._cache:
            self._cache["otel"] = self._probe("opentelemetry.sdk")
        return self._cache["otel"]

    @property
    def memory_semantic_search(self) -> bool:
        """True when optional deps for semantic search are available."""
        if "memory_semantic_search" not in self._cache:
            self._cache["memory_semantic_search"] = self.sentence_transformers
        return self._cache["memory_semantic_search"]

    def reset(self) -> None:
        """Clear the cached detection results (for test isolation)."""
        self._cache.clear()

    def as_dict(self) -> dict[str, bool]:
        """Return all flags (including derived) as a plain dict.

        All flags are evaluated and cached before returning, so the result
        always contains entries for: faiss, numpy, sentence_transformers,
        memory_semantic_search, and otel.
        """
        # Trigger evaluation so every flag is cached.
        _ = (
            self.faiss,
            self.numpy,
            self.sentence_transformers,
            self.memory_semantic_search,
            self.anthropic_sdk,
            self.openai_sdk,
            self.otel,
        )
        return dict(self._cache)


#: Module-level singleton.
feature_flags = FeatureFlags()
