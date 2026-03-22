"""Profile-based memory seeding.

Automatically seeds the memory store with facts detected by
``tapps_project_profile`` on first run. Seeded memories are tagged
with ``auto-seeded`` and ``source=system``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain.models import MemoryScope, MemorySource, MemoryTier

if TYPE_CHECKING:
    from tapps_brain._protocols import ProjectProfileLike
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEEDED_TAG = "auto-seeded"
_SOURCE_AGENT = "tapps-brain"
_SEEDED_FROM = "project_profile"
_DEFAULT_CONFIDENCE = 0.9


# ---------------------------------------------------------------------------
# Seeding logic
# ---------------------------------------------------------------------------


def _make_seed_tags(*extra: str) -> list[str]:
    """Build a tag list that always includes the auto-seeded marker."""
    return [_SEEDED_TAG, *extra]


def seed_from_profile(
    store: MemoryStore,
    profile: ProjectProfileLike,
) -> dict[str, Any]:
    """Seed memory store with facts from a project profile.

    Only seeds if the store is empty (first run). To re-seed, use
    :func:`reseed_from_profile` which updates only ``auto-seeded`` entries.

    Args:
        store: The memory store to seed.
        profile: Detected project profile.

    Returns:
        Summary dict with ``seeded_count``, ``skipped`` (bool, if non-empty).
    """
    if store.count() > 0:
        logger.info("memory_seed_skipped", reason="store not empty")
        return {"seeded_count": 0, "skipped": True}

    return _do_seed(store, profile)


def reseed_from_profile(
    store: MemoryStore,
    profile: ProjectProfileLike,
) -> dict[str, Any]:
    """Re-seed memory store, updating only auto-seeded entries.

    Never overwrites human/agent-created memories. Only updates
    entries tagged with ``auto-seeded``.

    Args:
        store: The memory store to reseed.
        profile: Detected project profile.

    Returns:
        Summary dict with ``seeded_count``, ``updated_count``.
    """
    # Delete existing auto-seeded memories
    existing = store.list_all(tags=[_SEEDED_TAG])
    deleted = 0
    for entry in existing:
        if _SEEDED_TAG in entry.tags:
            store.delete(entry.key)
            deleted += 1

    result = _do_seed(store, profile)
    result["deleted_old"] = deleted
    return result


def _do_seed(
    store: MemoryStore,
    profile: ProjectProfileLike,
) -> dict[str, Any]:
    """Internal: create seed memories from profile data."""
    seeded = 0

    # Project type
    if profile.project_type:
        confidence = max(_DEFAULT_CONFIDENCE, profile.project_type_confidence)
        store.save(
            key="project-type",
            value=f"Project type is {profile.project_type}",
            tier=MemoryTier.architectural.value,
            source=MemorySource.system.value,
            source_agent=_SOURCE_AGENT,
            scope=MemoryScope.project.value,
            tags=_make_seed_tags("project-type"),
            confidence=confidence,
            batch_context="seed",
        )
        _set_seeded_from(store, "project-type")
        seeded += 1

    # Languages
    for lang in profile.tech_stack.languages:
        key = f"language-{_slugify(lang)}"
        store.save(
            key=key,
            value=f"Project uses {lang}",
            tier=MemoryTier.architectural.value,
            source=MemorySource.system.value,
            source_agent=_SOURCE_AGENT,
            scope=MemoryScope.project.value,
            tags=_make_seed_tags("language"),
            confidence=_DEFAULT_CONFIDENCE,
            batch_context="seed",
        )
        _set_seeded_from(store, key)
        seeded += 1

    # Frameworks
    for fw in profile.tech_stack.frameworks:
        key = f"framework-{_slugify(fw)}"
        store.save(
            key=key,
            value=f"Project uses {fw} framework",
            tier=MemoryTier.architectural.value,
            source=MemorySource.system.value,
            source_agent=_SOURCE_AGENT,
            scope=MemoryScope.project.value,
            tags=_make_seed_tags("framework"),
            confidence=_DEFAULT_CONFIDENCE,
            batch_context="seed",
        )
        _set_seeded_from(store, key)
        seeded += 1

    # Test frameworks
    for tf in profile.test_frameworks:
        key = f"test-framework-{_slugify(tf)}"
        store.save(
            key=key,
            value=f"Project uses {tf} for testing",
            tier=MemoryTier.pattern.value,
            source=MemorySource.system.value,
            source_agent=_SOURCE_AGENT,
            scope=MemoryScope.project.value,
            tags=_make_seed_tags("test-framework"),
            confidence=_DEFAULT_CONFIDENCE,
            batch_context="seed",
        )
        _set_seeded_from(store, key)
        seeded += 1

    # Package managers
    for pm in profile.package_managers:
        key = f"package-manager-{_slugify(pm)}"
        store.save(
            key=key,
            value=f"Package manager is {pm}",
            tier=MemoryTier.pattern.value,
            source=MemorySource.system.value,
            source_agent=_SOURCE_AGENT,
            scope=MemoryScope.project.value,
            tags=_make_seed_tags("package-manager"),
            confidence=_DEFAULT_CONFIDENCE,
            batch_context="seed",
        )
        _set_seeded_from(store, key)
        seeded += 1

    # CI systems
    for ci in profile.ci_systems:
        key = f"ci-system-{_slugify(ci)}"
        store.save(
            key=key,
            value=f"Project uses {ci} for CI/CD",
            tier=MemoryTier.architectural.value,
            source=MemorySource.system.value,
            source_agent=_SOURCE_AGENT,
            scope=MemoryScope.project.value,
            tags=_make_seed_tags("ci-system"),
            confidence=_DEFAULT_CONFIDENCE,
            batch_context="seed",
        )
        _set_seeded_from(store, key)
        seeded += 1

    # Docker
    if profile.has_docker:
        store.save(
            key="has-docker",
            value="Project uses Docker",
            tier=MemoryTier.architectural.value,
            source=MemorySource.system.value,
            source_agent=_SOURCE_AGENT,
            scope=MemoryScope.project.value,
            tags=_make_seed_tags("docker"),
            confidence=_DEFAULT_CONFIDENCE,
            batch_context="seed",
        )
        _set_seeded_from(store, "has-docker")
        seeded += 1

    logger.info("memory_seeded", count=seeded)
    return {"seeded_count": seeded, "skipped": False}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert text to a simple slug for use as a memory key suffix."""
    return text.lower().replace(" ", "-").replace("_", "-")


def _set_seeded_from(store: MemoryStore, key: str) -> None:
    """Set the ``seeded_from`` field on a freshly seeded memory."""
    store.update_fields(key, seeded_from=_SEEDED_FROM)
