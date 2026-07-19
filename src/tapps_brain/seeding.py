"""Profile-based memory seeding.

Automatically seeds the memory store with facts detected by
``tapps_project_profile`` on first run. Seeded memories are tagged
with ``auto-seeded`` and ``source=system``.

**Save path:** each seed uses ``MemoryStore.save()`` inside a
:func:`~tapps_brain.rate_limiter.batch_exempt_scope` context, with
``skip_consolidation=True`` and ``conflict_check=False``: seeds are
independent facts produced by one deterministic detector, so merging or
contradiction-flagging sibling seeds (e.g. "Project uses C" vs "Project
uses C++") is always wrong. Existing entries without the ``auto-seeded``
tag are never overwritten.

**Profile version:** when ``MemoryProfile.seeding.seed_version`` is set, seed and
reseed summaries include ``profile_seed_version`` for operator diffing. The same
value is exposed on ``StoreHealthReport.profile_seed_version``, CLI
``maintenance health``, native ``run_health_check``, and MCP ``memory://stats``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain.models import MemoryScope, MemorySource, MemoryTier
from tapps_brain.rate_limiter import batch_exempt_scope

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

    with batch_exempt_scope("seed"):
        result = _do_seed(store, profile)
    return _with_seed_version(store, result)


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
        Summary dict with ``seeded_count``, ``skipped``, and ``deleted_old``
        (count of prior auto-seeded rows removed before re-creating).
    """
    # Delete existing auto-seeded memories.
    # list_all(tags=[_SEEDED_TAG]) already filters by tag, so no need to
    # re-check entry.tags here.
    existing = store.list_all(tags=[_SEEDED_TAG])
    deleted = 0
    for entry in existing:
        if store.delete(entry.key):
            deleted += 1

    with batch_exempt_scope("seed"):
        result = _do_seed(store, profile)
    result["deleted_old"] = deleted
    return _with_seed_version(store, result)


def _seed_one(
    store: MemoryStore,
    *,
    key: str,
    value: str,
    tier: MemoryTier,
    tag: str,
    confidence: float = _DEFAULT_CONFIDENCE,
) -> int:
    """Save one seed memory; return 1 on success, 0 on failure/skip (logged)."""
    # Contract: seeding never overwrites human/agent-created memories.
    # store.save is an upsert by key, so a human entry that happens to use a
    # seed key (e.g. "project-type") would be silently replaced without this
    # guard.  Only rows carrying the auto-seeded tag are fair game.
    existing = store.get(key)
    if existing is not None and _SEEDED_TAG not in (existing.tags or []):
        logger.info("seed_skipped_existing_entry", key=key)
        return 0
    saved = store.save(
        key=key,
        value=value,
        tier=tier.value,
        source=MemorySource.system.value,
        source_agent=_SOURCE_AGENT,
        scope=MemoryScope.project.value,
        tags=_make_seed_tags(tag),
        confidence=confidence,
        # Seeds are independent facts by construction; without these the
        # shared auto-seeded tag gives same-tier seeds >= 50% tag overlap,
        # so is_same_topic fires at the third seed and auto-consolidation
        # merges the fresh seeds into blobs (leaving most of them
        # contradicted) on any default-config store.  Likewise
        # conflict_check flags near-identical sibling values ("Project
        # uses C" vs "Project uses C++") as contradictions.
        skip_consolidation=True,
        conflict_check=False,
    )
    if isinstance(saved, dict):
        logger.warning("seed_save_failed", key=key, error=saved.get("error"))
        return 0
    _set_seeded_from(store, key)
    return 1


def _do_seed(
    store: MemoryStore,
    profile: ProjectProfileLike,
) -> dict[str, Any]:
    """Internal: create seed memories from profile data."""
    seeded = 0

    if profile.project_type:
        seeded += _seed_one(
            store,
            key="project-type",
            value=f"Project type is {profile.project_type}",
            tier=MemoryTier.architectural,
            tag="project-type",
            confidence=max(_DEFAULT_CONFIDENCE, profile.project_type_confidence),
        )

    for lang in profile.tech_stack.languages:
        if not lang:
            continue
        seeded += _seed_one(
            store,
            key=f"language-{_slugify(lang)}",
            value=f"Project uses {lang}",
            tier=MemoryTier.architectural,
            tag="language",
        )

    for fw in profile.tech_stack.frameworks:
        if not fw:
            continue
        seeded += _seed_one(
            store,
            key=f"framework-{_slugify(fw)}",
            value=f"Project uses {fw} framework",
            tier=MemoryTier.architectural,
            tag="framework",
        )

    for tf in profile.test_frameworks:
        if not tf:
            continue
        seeded += _seed_one(
            store,
            key=f"test-framework-{_slugify(tf)}",
            value=f"Project uses {tf} for testing",
            tier=MemoryTier.pattern,
            tag="test-framework",
        )

    for pm in profile.package_managers:
        if not pm:
            continue
        seeded += _seed_one(
            store,
            key=f"package-manager-{_slugify(pm)}",
            value=f"Package manager is {pm}",
            tier=MemoryTier.pattern,
            tag="package-manager",
        )

    for ci in profile.ci_systems:
        if not ci:
            continue
        seeded += _seed_one(
            store,
            key=f"ci-system-{_slugify(ci)}",
            value=f"Project uses {ci} for CI/CD",
            tier=MemoryTier.architectural,
            tag="ci-system",
        )

    if profile.has_docker:
        seeded += _seed_one(
            store,
            key="has-docker",
            value="Project uses Docker",
            tier=MemoryTier.architectural,
            tag="docker",
        )

    logger.info("memory_seeded", count=seeded)
    return {"seeded_count": seeded, "skipped": False}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Convert text to a simple slug for use as a memory key suffix.

    Transliterates key-illegal characters that distinguish real language
    names (``+`` -> ``p``, ``#`` -> ``sharp``) before stripping: plain
    removal collapsed ``C``, ``C++``, and ``C#`` to the same slug ``c``, so
    the later seed silently upserted over the earlier one and a language
    fact was lost.
    """
    slug = text.lower().strip().replace(" ", "-").replace("_", "-")
    slug = slug.replace("+", "p").replace("#", "sharp")
    slug = re.sub(r"[^a-z0-9._-]+", "-", slug)
    slug = re.sub(r"[-_.]{2,}", "-", slug).strip("-._")
    return slug or "x"


def _set_seeded_from(store: MemoryStore, key: str) -> None:
    """Set the ``seeded_from`` field on a freshly seeded memory."""
    store.update_fields(key, seeded_from=_SEEDED_FROM)
