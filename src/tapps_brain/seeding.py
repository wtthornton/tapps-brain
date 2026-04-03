"""Profile-based memory seeding.

Automatically seeds the memory store with facts detected by
``tapps_project_profile`` on first run. Seeded memories are tagged
with ``auto-seeded`` and ``source=system``.

**Save path:** each seed uses ``MemoryStore.save(..., batch_context="seed")``.
Save-time ``conflict_check`` runs like any other write; first-run seeding only
fires on an **empty** store, and ``reseed_from_profile`` deletes prior
``auto-seeded`` rows first, so collisions are rare. Custom integrators may call
``save(..., conflict_check=False)`` for deterministic bulk seeds when they
accept the risk (see tests).

**Profile version:** when ``MemoryProfile.seeding.seed_version`` is set, seed and
reseed summaries include ``profile_seed_version`` for operator diffing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain.models import MemoryScope, MemorySource, MemoryTier

if TYPE_CHECKING:
    from tapps_brain._protocols import ProjectProfileLike
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)


def _profile_seed_version(store: MemoryStore) -> str | None:
    """Return ``MemoryProfile.seeding.seed_version`` when the store has a profile."""
    prof = getattr(store, "_profile", None)
    if prof is None:
        return None
    seeding = getattr(prof, "seeding", None)
    if seeding is None:
        return None
    raw = getattr(seeding, "seed_version", None)
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def _with_seed_version(store: MemoryStore, payload: dict[str, Any]) -> dict[str, Any]:
    ver = _profile_seed_version(store)
    if ver is not None:
        out = dict(payload)
        out["profile_seed_version"] = ver
        return out
    return payload


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
        return _with_seed_version(store, {"seeded_count": 0, "skipped": True})

    return _with_seed_version(store, _do_seed(store, profile))


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
    # Delete existing auto-seeded memories.
    # list_all(tags=[_SEEDED_TAG]) already filters by tag, so no need to
    # re-check entry.tags here.
    existing = store.list_all(tags=[_SEEDED_TAG])
    deleted = 0
    for entry in existing:
        store.delete(entry.key)
        deleted += 1

    result = _do_seed(store, profile)
    result["deleted_old"] = deleted
    return _with_seed_version(store, result)


def _do_seed(  # noqa: PLR0915
    store: MemoryStore,
    profile: ProjectProfileLike,
) -> dict[str, Any]:
    """Internal: create seed memories from profile data."""
    seeded = 0

    # Project type
    if profile.project_type:
        confidence = max(_DEFAULT_CONFIDENCE, profile.project_type_confidence)
        saved = store.save(
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
        if not isinstance(saved, dict):
            _set_seeded_from(store, "project-type")
            seeded += 1
        else:
            logger.warning("seed_save_failed", key="project-type", error=saved.get("error"))

    # Languages
    for lang in profile.tech_stack.languages:
        if not lang:
            continue
        key = f"language-{_slugify(lang)}"
        saved = store.save(
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
        if not isinstance(saved, dict):
            _set_seeded_from(store, key)
            seeded += 1
        else:
            logger.warning("seed_save_failed", key=key, error=saved.get("error"))

    # Frameworks
    for fw in profile.tech_stack.frameworks:
        if not fw:
            continue
        key = f"framework-{_slugify(fw)}"
        saved = store.save(
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
        if not isinstance(saved, dict):
            _set_seeded_from(store, key)
            seeded += 1
        else:
            logger.warning("seed_save_failed", key=key, error=saved.get("error"))

    # Test frameworks
    for tf in profile.test_frameworks:
        if not tf:
            continue
        key = f"test-framework-{_slugify(tf)}"
        saved = store.save(
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
        if not isinstance(saved, dict):
            _set_seeded_from(store, key)
            seeded += 1
        else:
            logger.warning("seed_save_failed", key=key, error=saved.get("error"))

    # Package managers
    for pm in profile.package_managers:
        if not pm:
            continue
        key = f"package-manager-{_slugify(pm)}"
        saved = store.save(
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
        if not isinstance(saved, dict):
            _set_seeded_from(store, key)
            seeded += 1
        else:
            logger.warning("seed_save_failed", key=key, error=saved.get("error"))

    # CI systems
    for ci in profile.ci_systems:
        if not ci:
            continue
        key = f"ci-system-{_slugify(ci)}"
        saved = store.save(
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
        if not isinstance(saved, dict):
            _set_seeded_from(store, key)
            seeded += 1
        else:
            logger.warning("seed_save_failed", key=key, error=saved.get("error"))

    # Docker
    if profile.has_docker:
        saved = store.save(
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
        if not isinstance(saved, dict):
            _set_seeded_from(store, "has-docker")
            seeded += 1
        else:
            logger.warning("seed_save_failed", key="has-docker", error=saved.get("error"))

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
