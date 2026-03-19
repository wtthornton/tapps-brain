"""Content safety - prompt injection detection for stored and retrieved content.

All content passes through this filter before being stored or injected.
Content is *untrusted* - it may contain prompt injection attempts.

Extracted from tapps_core.security.content_safety for standalone use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)

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
    # Role manipulation
    (
        "role_manipulation",
        re.compile(
            r"you\s+are\s+now\s+(?:an?\s+)?(?:new|different|evil|unrestricted|malicious|jailbroken)",
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


@dataclass
class SafetyCheckResult:
    """Result of RAG safety check on retrieved content."""

    safe: bool = True
    flagged_patterns: list[str] = field(default_factory=list)
    match_count: int = 0
    sanitised_content: str | None = None
    warning: str | None = None


def check_content_safety(content: str) -> SafetyCheckResult:
    """Check content for prompt injection.

    Args:
        content: Content to check.

    Returns:
        ``SafetyCheckResult`` with safety assessment and optionally
        sanitised content.
    """
    if not content or not content.strip():
        return SafetyCheckResult(safe=True)

    flagged: list[str] = []
    total_matches = 0

    for pattern_name, pattern in _INJECTION_PATTERNS:
        matches = pattern.findall(content)
        if matches:
            flagged.append(pattern_name)
            total_matches += len(matches)

    # Check density of suspicious patterns
    lines = content.splitlines()
    suspicious_lines = 0
    for line in lines:
        for _, pattern in _INJECTION_PATTERNS:
            if pattern.search(line):
                suspicious_lines += 1
                break

    high_density = (
        len(lines) > 0 and (suspicious_lines / len(lines)) > _SUSPICIOUS_DENSITY_THRESHOLD
    )

    if total_matches == 0:
        return SafetyCheckResult(safe=True)

    if total_matches > _MAX_PATTERN_MATCHES or high_density:
        # Too many matches - reject entirely
        logger.warning(
            "rag_safety_blocked",
            match_count=total_matches,
            patterns=flagged,
            density=round(suspicious_lines / max(len(lines), 1), 3),
        )
        return SafetyCheckResult(
            safe=False,
            flagged_patterns=flagged,
            match_count=total_matches,
            warning=(
                f"Content blocked: {total_matches} prompt injection patterns detected "
                f"({', '.join(flagged)})"
            ),
        )

    # Low match count - sanitise and warn
    sanitised = _sanitise_content(content)
    logger.info(
        "rag_safety_warning",
        match_count=total_matches,
        patterns=flagged,
    )
    return SafetyCheckResult(
        safe=True,
        flagged_patterns=flagged,
        match_count=total_matches,
        sanitised_content=sanitised,
        warning=f"Minor injection patterns detected and sanitised ({', '.join(flagged)})",
    )


def _sanitise_content(content: str) -> str:
    """Remove or neutralise detected injection patterns from content."""
    result = content
    for _name, pattern in _INJECTION_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result
