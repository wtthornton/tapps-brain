"""Contradiction detection for memory entries.

Compares memories against observable project state (tech stack, file
existence, test frameworks, package managers, branches) to detect
stale or incorrect information. All detection is deterministic —
no LLM calls.

Save-time similarity conflicts (:func:`detect_save_conflicts`) stay on that
deterministic path. Optional neural NLI labeling belongs in offline jobs only;
see ``docs/guides/save-conflict-nli-offline.md`` and
:func:`tapps_brain.evaluation.run_save_conflict_candidate_report`.

STORY-SC03 (TAP-559): :func:`detect_pairwise_contradictions` scans pairs of
memory entries for keyword polarity ("uses Postgres" vs "uses SQLite"), numeric
divergence (same metric label, different value) and boolean polarity
("enabled" vs "disabled").  Results are :class:`PolarityContradiction` instances
so that both sides can be preserved with ``audit.supersedes`` edges rather than
silently dropped.
"""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple

import structlog
from pydantic import BaseModel, Field

from tapps_brain.models import MemoryEntry

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain._protocols import ProjectProfileLike

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Save-time conflict detection (GitHub #44, task 040.16)
# ---------------------------------------------------------------------------


class SaveConflictHit(NamedTuple):
    """One existing entry flagged as conflicting with an incoming save."""

    entry: MemoryEntry
    similarity: float


def format_save_conflict_reason(
    *,
    incoming_key: str,
    tier: str,
    similarity: float,
) -> str:
    """Deterministic user-visible / audit text for save-time conflict invalidation."""
    sim_r = round(float(similarity), 4)
    return (
        f"Save-time conflict: invalidated by incoming memory '{incoming_key}' "
        f"(tier={tier}, similarity={sim_r})."
    )


def detect_save_conflicts(
    new_value: str,
    new_tier: str,
    existing_entries: list[MemoryEntry],
    similarity_threshold: float = 0.6,
    *,
    exclude_key: str | None = None,
) -> list[SaveConflictHit]:
    """Detect existing entries that may conflict with a new value.

    Looks for entries in the same tier with high text similarity but
    different content (potential contradiction).

    Args:
        new_value: The value about to be saved.
        new_tier: The tier of the entry about to be saved.
        existing_entries: All entries to scan for conflicts.
        similarity_threshold: Entries with similarity above this threshold
            (but not identical) are considered potential conflicts.
        exclude_key: Entry key being overwritten by this save. That row is
            never treated as a separate conflicting memory (avoids invalidating
            the head then rebuilding it with the same ``valid_at``/``invalid_at``).

    Returns:
        Hits sorted by similarity descending, then key (deterministic). Each hit
        includes the similarity score used for audit / ``contradiction_reason``.
    """
    from tapps_brain.similarity import text_similarity

    def _normalize(text: str) -> str:
        return " ".join(text.lower().split())

    new_value_norm = _normalize(new_value)

    # Build a temporary entry for text_similarity (needs a MemoryEntry)
    _sentinel = MemoryEntry(
        key="conflict-sentinel",
        value=new_value,
        tier=new_tier,
    )

    scored: list[tuple[float, MemoryEntry]] = []
    for entry in existing_entries:
        if exclude_key is not None and entry.key == exclude_key:
            continue
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
    return [SaveConflictHit(entry=e, similarity=s) for s, e in scored]


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


# ---------------------------------------------------------------------------
# Pairwise polarity & numeric-divergence contradiction detection (TAP-559)
# ---------------------------------------------------------------------------


class PolarityContradiction(NamedTuple):
    """A detected polarity or numeric contradiction between two memory entries.

    Unlike :class:`Contradiction` (which compares a memory to observable project
    state), ``PolarityContradiction`` flags a pair of memories that contradict
    each other.  Both entries are preserved — callers can write
    ``audit.supersedes`` / ``audit.superseded_by`` edges rather than silently
    dropping one side.
    """

    entry_a_key: str
    entry_b_key: str
    reason: str
    contradiction_type: str  # "keyword_polarity" | "numeric_divergence" | "boolean_polarity"
    detected_at: str = ""


# Regex patterns for extracting semantic signals.
_USE_PATTERN: re.Pattern[str] = re.compile(
    r"(?:we\s+)?(?:use[sd]?|using|built\s+(?:with|on)|migrated?\s+to|switched?\s+to)"
    r"\s+([A-Za-z][\w.-]*)",
    re.IGNORECASE,
)

# Label + numeric value, e.g. "threshold is 0.7", "timeout = 30", "max_entries: 5000".
_NUMERIC_LABEL_PATTERN: re.Pattern[str] = re.compile(
    r"([\w][\w\s]{1,30}?)\s+(?:is|=|:|are|was|set\s+to)\s+(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

# Boolean state, e.g. "auto_migrate is enabled", "logging is true".
_BOOL_PATTERN: re.Pattern[str] = re.compile(
    r"([\w][\w\s]{1,30}?)\s+(?:is|was)\s+(enabled|disabled|true|false|on|off)",
    re.IGNORECASE,
)

_BOOL_OPPOSITES: dict[str, str] = {
    "enabled": "disabled",
    "disabled": "enabled",
    "true": "false",
    "false": "true",
    "on": "off",
    "off": "on",
}

# Relative divergence threshold: values must differ by at least this fraction
# of the larger value to be flagged.
_NUMERIC_DIVERGENCE_THRESHOLD = 0.15


def detect_keyword_polarity(
    entry_a: MemoryEntry,
    entry_b: MemoryEntry,
) -> PolarityContradiction | None:
    """Detect conflicting technology claims (e.g., 'uses Postgres' vs 'uses SQLite').

    Extracts technology names from ``uses / using / built with / migrated to``
    patterns in both entries.  When both entries make technology claims that are
    entirely disjoint (no common tech), the pair is flagged as a keyword polarity
    contradiction.

    Args:
        entry_a: First memory entry.
        entry_b: Second memory entry.

    Returns:
        A :class:`PolarityContradiction` if conflicting claims are found, else ``None``.
    """
    techs_a = {m.lower() for m in _USE_PATTERN.findall(entry_a.value)}
    techs_b = {m.lower() for m in _USE_PATTERN.findall(entry_b.value)}

    if not techs_a or not techs_b:
        return None

    # Fully disjoint technology claims → contradiction.
    if not (techs_a & techs_b):
        rep_a = sorted(techs_a)[0]
        rep_b = sorted(techs_b)[0]
        return PolarityContradiction(
            entry_a_key=entry_a.key,
            entry_b_key=entry_b.key,
            reason=f"Conflicting technology claims: '{rep_a}' vs '{rep_b}'",
            contradiction_type="keyword_polarity",
            detected_at=datetime.now(tz=UTC).isoformat(),
        )

    return None


def detect_numeric_divergence(
    entry_a: MemoryEntry,
    entry_b: MemoryEntry,
    *,
    divergence_threshold: float = _NUMERIC_DIVERGENCE_THRESHOLD,
) -> PolarityContradiction | None:
    """Detect the same metric label carrying divergent numeric values across two entries.

    Scans for ``<label> is/= <number>`` patterns, then checks whether a shared
    label has values that differ by more than *divergence_threshold* (relative to
    the larger value).

    Args:
        entry_a: First memory entry.
        entry_b: Second memory entry.
        divergence_threshold: Minimum relative difference to flag (default 0.15 = 15%).

    Returns:
        A :class:`PolarityContradiction` for the first divergent label found, or ``None``.
    """
    nums_a = {
        label.strip().lower(): float(val)
        for label, val in _NUMERIC_LABEL_PATTERN.findall(entry_a.value)
    }
    nums_b = {
        label.strip().lower(): float(val)
        for label, val in _NUMERIC_LABEL_PATTERN.findall(entry_b.value)
    }

    for label, val_a in sorted(nums_a.items()):
        if label not in nums_b:
            continue
        val_b = nums_b[label]
        base = max(abs(val_a), abs(val_b), 1.0)
        if abs(val_a - val_b) / base > divergence_threshold:
            return PolarityContradiction(
                entry_a_key=entry_a.key,
                entry_b_key=entry_b.key,
                reason=f"Numeric divergence for '{label}': {val_a} vs {val_b}",
                contradiction_type="numeric_divergence",
                detected_at=datetime.now(tz=UTC).isoformat(),
            )

    return None


def detect_boolean_polarity(
    entry_a: MemoryEntry,
    entry_b: MemoryEntry,
) -> PolarityContradiction | None:
    """Detect boolean polarity flips (e.g., 'auto_migrate is enabled' vs 'disabled').

    Scans for ``<label> is <enabled|disabled|true|false|on|off>`` patterns and
    flags pairs where the same label has opposite boolean states.

    Args:
        entry_a: First memory entry.
        entry_b: Second memory entry.

    Returns:
        A :class:`PolarityContradiction` for the first polarity flip found, or ``None``.
    """
    bools_a = {
        label.strip().lower(): state.lower()
        for label, state in _BOOL_PATTERN.findall(entry_a.value)
    }
    bools_b = {
        label.strip().lower(): state.lower()
        for label, state in _BOOL_PATTERN.findall(entry_b.value)
    }

    for label in sorted(bools_a):
        if label not in bools_b:
            continue
        val_a = bools_a[label]
        val_b = bools_b[label]
        if _BOOL_OPPOSITES.get(val_a) == val_b:
            return PolarityContradiction(
                entry_a_key=entry_a.key,
                entry_b_key=entry_b.key,
                reason=f"Boolean polarity conflict for '{label}': {val_a} vs {val_b}",
                contradiction_type="boolean_polarity",
                detected_at=datetime.now(tz=UTC).isoformat(),
            )

    return None


def detect_pairwise_contradictions(
    entries: list[MemoryEntry],
) -> list[PolarityContradiction]:
    """Scan all active entry pairs for polarity and numeric contradictions.

    Runs :func:`detect_keyword_polarity`, :func:`detect_numeric_divergence`, and
    :func:`detect_boolean_polarity` on every unique pair of non-contradicted entries.
    At most one contradiction is reported per pair (first match wins).

    Pairs are examined in deterministic sorted order so results are stable across
    calls with the same input.

    Args:
        entries: Memory entries to scan (contradicted entries are skipped).

    Returns:
        List of :class:`PolarityContradiction` instances, at most one per pair.
    """
    active = [e for e in entries if not getattr(e, "contradicted", False)]
    sorted_keys = sorted(e.key for e in active)
    entry_map: dict[str, MemoryEntry] = {e.key: e for e in active}

    results: list[PolarityContradiction] = []

    for i, key_a in enumerate(sorted_keys):
        for key_b in sorted_keys[i + 1 :]:
            ea = entry_map[key_a]
            eb = entry_map[key_b]
            hit = (
                detect_keyword_polarity(ea, eb)
                or detect_numeric_divergence(ea, eb)
                or detect_boolean_polarity(ea, eb)
            )
            if hit:
                results.append(hit)
                logger.debug(
                    "pairwise_contradiction_detected",
                    type=hit.contradiction_type,
                    key_a=key_a,
                    key_b=key_b,
                )

    return results
