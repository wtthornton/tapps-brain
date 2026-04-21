"""Content safety - prompt injection detection for stored and retrieved content.

All content passes through this filter before being stored or injected.
Content is *untrusted* - it may contain prompt injection attempts.

Extracted from tapps_core.security.content_safety for standalone use.

EPIC-044 STORY-044.1: versioned pattern rulesets (semver keys) and optional
``MetricsCollector`` increments for block vs sanitize outcomes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from tapps_brain.metrics import MetricsCollector

logger = structlog.get_logger(__name__)

# Semver keys for bundled pattern sets (extend when rules change materially).
DEFAULT_SAFETY_RULESET_VERSION: str = "1.0.0"
SUPPORTED_SAFETY_RULESET_VERSIONS: frozenset[str] = frozenset({DEFAULT_SAFETY_RULESET_VERSION})

# ------------------------------------------------------------------
# Injection patterns
# ------------------------------------------------------------------

# Patterns that indicate prompt injection attempts in documentation
_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Direct instruction override attempts
    (
        "instruction_override",
        re.compile(
            r"(?:ignore|forget|disregard)\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+"
            r"(?:instructions?|prompts?|rules?|context)",
            re.IGNORECASE,
        ),
    ),
    # System prompt extraction
    (
        "system_prompt_extract",
        re.compile(
            r"(?:reveal|show|print|output|display|repeat)\s+(?:your\s+)?(?:system\s+)?prompt",
            re.IGNORECASE,
        ),
    ),
    # Role manipulation — covers "you are now", "act as", "pretend to be", "roleplay as"
    (
        "role_manipulation",
        re.compile(
            r"(?:"
            r"you\s+are\s+now"
            r"|act\s+as"
            r"|pretend\s+(?:you\s+are|to\s+be)"
            r"|roleplay\s+as"
            r")\s+(?:an?\s+)?(?:new|different|evil|unrestricted|malicious|jailbroken|unfiltered|uncensored|DAN)",
            re.IGNORECASE,
        ),
    ),
    # Hidden instruction markers (escaped pipes to match literal <|...|> tokens)
    (
        "hidden_instruction",
        re.compile(
            r"<\|(?:im_start|im_end|system|user|assistant)\|>",
            re.IGNORECASE,
        ),
    ),
    # Delimiter-based injection
    (
        "delimiter_injection",
        re.compile(
            r"(?:---+\s*(?:SYSTEM|USER|ASSISTANT|INSTRUCTION)\s*---+)",
            re.IGNORECASE,
        ),
    ),
    # Base64 obfuscation in non-code context
    (
        "base64_obfuscation",
        re.compile(
            r"(?:eval|exec|execute)\s*\(\s*(?:base64|b64decode|atob)\s*\(",
            re.IGNORECASE,
        ),
    ),
]

# Suspicious content density threshold - if more than this fraction of
# lines contain suspicious patterns, flag the entire document
_SUSPICIOUS_DENSITY_THRESHOLD = 0.15

# Maximum pattern matches before flagging entire document
_MAX_PATTERN_MATCHES = 5


def resolve_safety_ruleset_version(requested: str | None) -> str:
    """Return a supported ruleset semver, falling back to the default if unknown."""
    if requested is None or not str(requested).strip():
        return DEFAULT_SAFETY_RULESET_VERSION
    v = str(requested).strip()
    if v in SUPPORTED_SAFETY_RULESET_VERSIONS:
        return v
    logger.warning(
        "rag_safety_unknown_ruleset_version",
        requested=v,
        fallback=DEFAULT_SAFETY_RULESET_VERSION,
    )
    return DEFAULT_SAFETY_RULESET_VERSION


@dataclass
class SafetyCheckResult:
    """Result of RAG safety check on retrieved content."""

    safe: bool = True
    flagged_patterns: list[str] = field(default_factory=list)
    match_count: int = 0
    sanitised_content: str | None = None
    warning: str | None = None
    ruleset_version: str = ""


def check_content_safety(
    content: str,
    *,
    ruleset_version: str | None = None,
    metrics: MetricsCollector | None = None,
) -> SafetyCheckResult:
    """Check content for prompt injection.

    Args:
        content: Content to check.
        ruleset_version: Optional semver pin (profile ``safety.ruleset_version``).
            Unknown values log a warning and fall back to
            ``DEFAULT_SAFETY_RULESET_VERSION``.
        metrics: When set, increments ``rag_safety.blocked`` or
            ``rag_safety.sanitized`` on block vs sanitize outcomes (clean passes
            do not increment).

    Returns:
        ``SafetyCheckResult`` with safety assessment, effective ``ruleset_version``,
        and optionally sanitised content.
    """
    resolved = resolve_safety_ruleset_version(ruleset_version)
    patterns = _injection_patterns_for_version(resolved)

    if not content or not content.strip():
        return SafetyCheckResult(safe=True, ruleset_version=resolved)

    # Normalise unicode (NFKC) to defeat homograph/lookalike bypass attempts
    # e.g. "Ɨgnore" → "Ignore", "ｉgnore" → "ignore"  # noqa: RUF003
    normalised = unicodedata.normalize("NFKC", content)

    flagged: list[str] = []
    total_matches = 0

    for pattern_name, pattern in patterns:
        matches = pattern.findall(normalised)
        if matches:
            flagged.append(pattern_name)
            total_matches += len(matches)

    # Short-circuit before the more expensive per-line density scan
    if total_matches == 0:
        return SafetyCheckResult(safe=True, ruleset_version=resolved)

    # Check density of suspicious patterns
    lines = normalised.splitlines()
    suspicious_lines = 0
    for line in lines:
        for _, pattern in patterns:
            if pattern.search(line):
                suspicious_lines += 1
                break

    high_density = (
        len(lines) > 0 and (suspicious_lines / len(lines)) > _SUSPICIOUS_DENSITY_THRESHOLD
    )

    if total_matches > _MAX_PATTERN_MATCHES or high_density:
        # Too many matches - reject entirely
        logger.warning(
            "rag_safety_blocked",
            match_count=total_matches,
            patterns=flagged,
            density=round(suspicious_lines / max(len(lines), 1), 3),
            ruleset_version=resolved,
        )
        result = SafetyCheckResult(
            safe=False,
            flagged_patterns=flagged,
            match_count=total_matches,
            warning=(
                f"Content blocked: {total_matches} prompt injection patterns detected "
                f"({', '.join(flagged)})"
            ),
            ruleset_version=resolved,
        )
        if metrics is not None:
            metrics.increment("rag_safety.blocked")
        return result

    # Low match count - sanitise and warn.  Run the regexes against the
    # *original* content so callers receive their exact bytes back with only
    # the injection substrings replaced.  NFKC normalisation is used solely
    # for detection; we deliberately do not rewrite the user's Unicode.
    sanitised = _sanitise_content(content, patterns)
    logger.info(
        "rag_safety_warning",
        match_count=total_matches,
        patterns=flagged,
        ruleset_version=resolved,
    )
    result = SafetyCheckResult(
        safe=True,
        flagged_patterns=flagged,
        match_count=total_matches,
        sanitised_content=sanitised,
        warning=f"Minor injection patterns detected and sanitised ({', '.join(flagged)})",
        ruleset_version=resolved,
    )
    if metrics is not None:
        metrics.increment("rag_safety.sanitized")
    return result


def _injection_patterns_for_version(version: str) -> list[tuple[str, re.Pattern[str]]]:
    """Pattern table for a resolved ruleset version (extend when adding rule bundles)."""
    _ = version  # future: dispatch on semver
    return _INJECTION_PATTERNS


def _sanitise_content(
    content: str,
    patterns: list[tuple[str, re.Pattern[str]]],
) -> str:
    """Remove or neutralise detected injection patterns from content."""
    result = content
    for _name, pattern in patterns:
        result = pattern.sub("[REDACTED]", result)
    return result
