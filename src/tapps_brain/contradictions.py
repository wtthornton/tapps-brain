"""Contradiction detection for memory entries.

Compares memories against observable project state (tech stack, file
existence, test frameworks, package managers, branches) to detect
stale or incorrect information. All detection is deterministic -
no LLM calls.
"""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain._protocols import ProjectProfileLike
    from tapps_brain.models import MemoryEntry

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Save-time conflict detection (GitHub #44, task 040.16)
# ---------------------------------------------------------------------------


def detect_save_conflicts(
    new_value: str,
    new_tier: str,
    existing_entries: list[MemoryEntry],
    similarity_threshold: float = 0.6,
) -> list[MemoryEntry]:
    """Detect existing entries that may conflict with a new value.

    Looks for entries in the same tier with high text similarity but
    different content (potential contradiction).

    Args:
        new_value: The value about to be saved.
        new_tier: The tier of the entry about to be saved.
        existing_entries: All entries to scan for conflicts.
        similarity_threshold: Entries with similarity above this threshold
            (but not identical) are considered potential conflicts.

    Returns:
        List of potentially conflicting entries, sorted by similarity descending.
    """
    from tapps_brain.models import MemoryEntry as _MemoryEntry  # local import to avoid cycles
    from tapps_brain.similarity import text_similarity

    def _normalize(text: str) -> str:
        return " ".join(text.lower().split())

    new_value_norm = _normalize(new_value)

    # Build a temporary entry for text_similarity (needs a MemoryEntry)
    _sentinel = _MemoryEntry(
        key="conflict-sentinel",
        value=new_value,
        tier=new_tier,
    )

    scored: list[tuple[float, _MemoryEntry]] = []
    for entry in existing_entries:
        # Only compare entries in the same tier
        entry_tier = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
        if entry_tier != new_tier:
            continue

        # Skip identical entries (normalized)
        if _normalize(entry.value) == new_value_norm:
            continue

        sim = text_similarity(_sentinel, entry)
        if sim > similarity_threshold:
            scored.append((sim, entry))

    # Sort by similarity descending, then by key for determinism
    scored.sort(key=lambda t: (-t[0], t[1].key))
    return [entry for _, entry in scored]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Contradiction(BaseModel):
    """A detected contradiction between a memory and project state."""

    memory_key: str = Field(description="Key of the contradicted memory.")
    reason: str = Field(description="Human-readable explanation.")
    evidence: str = Field(description="What project state was compared.")
    detected_at: str = Field(
        default_factory=lambda: datetime.now(tz=UTC).isoformat(),
        description="ISO-8601 UTC detection timestamp.",
    )


# ---------------------------------------------------------------------------
# Tag sets that trigger specific checks
# ---------------------------------------------------------------------------

_TECH_TAGS = frozenset({"library", "framework", "database", "orm", "dependency"})
_FILE_TAGS = frozenset({"file", "path", "module"})
_TEST_TAGS = frozenset({"test", "testing", "test-framework"})
_PKG_TAGS = frozenset({"package-manager", "build-tool", "tooling"})
_BRANCH_TAGS = frozenset({"branch", "feature-branch"})

# Minimum claimed library name length to avoid false positives on short words.
_MIN_CLAIMED_NAME_LENGTH = 3

# Patterns for extracting tech claims from memory values.
_CLAIM_PATTERNS = [
    r"(?:we\s+)?use[sd]?\s+(\w[\w.-]*)",
    r"using\s+(\w[\w.-]*)",
    r"built\s+(?:with|on)\s+(\w[\w.-]*)",
    r"migrated?\s+to\s+(\w[\w.-]*)",
    r"switched?\s+to\s+(\w[\w.-]*)",
]

# Known test framework names for contradiction checking.
_TEST_FW_NAMES = ["pytest", "jest", "mocha", "go-test", "cargo-test", "unittest"]

# Known package manager names for contradiction checking.
_PM_NAMES = ["pip", "uv", "poetry", "npm", "yarn", "pnpm", "cargo", "go-mod"]


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class ContradictionDetector:
    """Detects memories that contradict observable project state."""

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root

    def detect_contradictions(
        self,
        memories: list[MemoryEntry],
        profile: ProjectProfileLike,
    ) -> list[Contradiction]:
        """Run all contradiction checks against the given memories.

        Returns a list of contradictions found. Each memory is checked
        at most once per rule type.
        """
        contradictions: list[Contradiction] = []
        for entry in memories:
            contradictions.extend(self._check_entry(entry, profile))
        return contradictions

    def _check_entry(
        self,
        entry: MemoryEntry,
        profile: ProjectProfileLike,
    ) -> list[Contradiction]:
        """Run all applicable checks on a single memory entry."""
        tags_lower = frozenset(t.lower() for t in entry.tags)
        candidates = [
            self._check_tech_stack(entry, profile) if tags_lower & _TECH_TAGS else None,
            self._check_file_existence(entry) if tags_lower & _FILE_TAGS else None,
            self._check_test_frameworks(entry, profile) if tags_lower & _TEST_TAGS else None,
            self._check_package_managers(entry, profile) if tags_lower & _PKG_TAGS else None,
            self._check_branch_existence(entry) if tags_lower & _BRANCH_TAGS else None,
        ]
        return [c for c in candidates if c is not None]

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_tech_stack(
        self, entry: MemoryEntry, profile: ProjectProfileLike
    ) -> Contradiction | None:
        """Check if a memory references a library/framework not in the project.

        Early-return design: if any known tech appears in the memory, we treat
        the memory as "probably correct" and skip claim extraction.  This is
        deliberate — it trades missed detections on memories that mention both a
        known and an unknown tech for a significant reduction in false positives
        (e.g. "migrated from Flask to Django" where both are later added to the
        stack).
        """
        known = set(profile.tech_stack.libraries) | set(profile.tech_stack.frameworks)
        known_lower = {k.lower() for k in known}

        value_lower = entry.value.lower()
        for lib in known_lower:
            if re.search(rf"\b{re.escape(lib)}\b", value_lower):
                return None  # found in both memory and project

        return self._check_tech_claims(entry.key, value_lower, known_lower)

    def _check_tech_claims(
        self, key: str, value_lower: str, known_lower: set[str]
    ) -> Contradiction | None:
        """Extract tech claims from text and check against known stack."""
        for pattern in _CLAIM_PATTERNS:
            match = re.search(pattern, value_lower)
            if match:
                claimed = match.group(1)
                if claimed not in known_lower and len(claimed) > _MIN_CLAIMED_NAME_LENGTH:
                    contradiction = Contradiction(
                        memory_key=key,
                        reason=(
                            f"Memory claims use of '{claimed}' but it was not "
                            f"found in the project tech stack."
                        ),
                        evidence=f"tech_stack.libraries={sorted(known_lower)}",
                    )
                    logger.debug(
                        "contradiction_detected",
                        check="tech_stack",
                        key=key,
                        claimed=claimed,
                    )
                    return contradiction
        return None

    def _check_file_existence(self, entry: MemoryEntry) -> Contradiction | None:
        """Check if a memory references a file path that no longer exists."""
        file_patterns = re.findall(r"(?:^|[\s\"'`(])([a-zA-Z0-9_./-]+\.[a-zA-Z0-9]+)", entry.value)

        for fpath in file_patterns:
            if fpath.startswith(("/", "\\")) or ".." in fpath:
                continue
            # Skip pure version strings like "1.2.3" or "2.0.1" — not file paths.
            if re.match(r"^\d+(\.\d+)*$", fpath):
                continue
            full = self._project_root / fpath
            try:
                full.resolve().relative_to(self._project_root.resolve())
            except ValueError:
                continue
            if not full.exists():
                contradiction = Contradiction(
                    memory_key=entry.key,
                    reason=f"Memory references file '{fpath}' which no longer exists.",
                    evidence=f"Path checked: {full}",
                )
                logger.debug(
                    "contradiction_detected",
                    check="file_existence",
                    key=entry.key,
                    path=fpath,
                )
                return contradiction

        return None

    def _check_test_frameworks(
        self, entry: MemoryEntry, profile: ProjectProfileLike
    ) -> Contradiction | None:
        """Check if a memory mentions a test framework not detected in the project."""
        known = {f.lower() for f in profile.test_frameworks}
        if not known:
            return None

        value_lower = entry.value.lower()
        for fw in _TEST_FW_NAMES:
            if re.search(rf"\b{re.escape(fw)}\b", value_lower) and fw not in known:
                contradiction = Contradiction(
                    memory_key=entry.key,
                    reason=(
                        f"Memory mentions test framework '{fw}' but project uses {sorted(known)}."
                    ),
                    evidence=f"test_frameworks={sorted(known)}",
                )
                logger.debug(
                    "contradiction_detected",
                    check="test_framework",
                    key=entry.key,
                    claimed_fw=fw,
                )
                return contradiction

        return None

    def _check_package_managers(
        self, entry: MemoryEntry, profile: ProjectProfileLike
    ) -> Contradiction | None:
        """Check if a memory mentions a package manager not detected in the project."""
        known = {p.lower() for p in profile.package_managers}
        if not known:
            return None

        value_lower = entry.value.lower()
        for pm in _PM_NAMES:
            if re.search(rf"\b{re.escape(pm)}\b", value_lower) and pm not in known:
                contradiction = Contradiction(
                    memory_key=entry.key,
                    reason=(
                        f"Memory mentions package manager '{pm}' but project uses {sorted(known)}."
                    ),
                    evidence=f"package_managers={sorted(known)}",
                )
                logger.debug(
                    "contradiction_detected",
                    check="package_manager",
                    key=entry.key,
                    claimed_pm=pm,
                )
                return contradiction

        return None

    def _check_branch_existence(self, entry: MemoryEntry) -> Contradiction | None:
        """Check if a branch-scoped memory references a branch that no longer exists."""
        if not entry.branch:
            return None

        try:
            result = subprocess.run(
                ["git", "branch", "--list", entry.branch],
                capture_output=True,
                text=True,
                cwd=str(self._project_root),
                timeout=5,
                check=False,
            )
            if result.returncode != 0:
                # Not a git repository or git error — skip check rather than false-positive.
                logger.debug(
                    "branch_check_skipped",
                    branch=entry.branch,
                    returncode=result.returncode,
                    stderr=result.stderr.strip(),
                )
                return None
            branches = [
                b.strip().removeprefix("* ").strip() for b in result.stdout.strip().splitlines()
            ]
            if entry.branch not in branches:
                contradiction = Contradiction(
                    memory_key=entry.key,
                    reason=(f"Memory is scoped to branch '{entry.branch}' which no longer exists."),
                    evidence="git branch --list",
                )
                logger.debug(
                    "contradiction_detected",
                    check="branch_existence",
                    key=entry.key,
                    branch=entry.branch,
                )
                return contradiction
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.debug("branch_check_failed", branch=entry.branch, error=str(exc))

        return None
