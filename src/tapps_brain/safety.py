"""Content safety - prompt injection detection for stored and retrieved content.

All content passes through this filter before being stored or injected.
Content is *untrusted* - it may contain prompt injection attempts.

Extracted from tapps_core.security.content_safety for standalone use.

EPIC-044 STORY-044.1: versioned pattern rulesets (semver keys) and optional
``MetricsCollector`` increments for block vs sanitize outcomes.
"""

from __future__ import annotations

import base64
import math
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

# ------------------------------------------------------------------
# Encoded-payload detector (TAP-1813)
# ------------------------------------------------------------------

# Minimum encoded-token length to attempt decoding (≥24 chars = ≥18 raw bytes)
_MIN_ENCODED_LENGTH = 24

# Maximum decoded size before rejecting (avoids DoS on huge blobs)
_MAX_DECODED_BYTES = 32 * 1024  # 32 KiB

# Shannon-entropy ceiling for decoded text (bits/byte).
# Human-readable text is typically 3.5-4.5 b/byte; binary blobs approach 8.
# A threshold of 5.0 reliably rejects crypto keys, compressed data, and image
# pixels while passing Unicode-rich injection phrases (e.g. with emoji prefix).
_MAX_TEXT_ENTROPY_BITS: float = 5.0

# Upper bound on encoded token length matched by the regexes.
# Prevents the regex engine from materialising a giant match group before
# _decode_as_text can apply _MAX_DECODED_BYTES.  44 000 base64 chars ≈ 33 KiB
# decoded — just above _MAX_DECODED_BYTES (32 KiB), so the decoder never
# fires on tokens larger than the size gate would reject anyway.
_MAX_B64_TOKEN_CHARS = 44_000  # ≈ 32 KiB decoded
_MAX_HEX_TOKEN_CHARS = 65_536  # 32 KiB decoded * 2 hex chars/byte

# Regex for standalone standard-base64 tokens (A-Za-z0-9+/).
# Lookbehind/lookahead on the same charset prevents mid-token matching.
_B64_TOKEN_RE = re.compile(
    rf"(?<![A-Za-z0-9+/])([A-Za-z0-9+/]{{24,{_MAX_B64_TOKEN_CHARS}}}={{0,2}})(?![A-Za-z0-9+/=])"
)

# Regex for standalone url-safe-base64 tokens (A-Za-z0-9-_).
# We only process these when the token contains '-' or '_' (otherwise the
# standard-b64 path already covers them).
_URLSAFE_B64_TOKEN_RE = re.compile(
    rf"(?<![A-Za-z0-9\-_])([A-Za-z0-9\-_]{{24,{_MAX_B64_TOKEN_CHARS}}}={{0,2}})(?![A-Za-z0-9\-_=])"
)

# Regex for standalone hex strings (even number of hex digits, ≥48 chars = ≥24 raw bytes).
_HEX_TOKEN_RE = re.compile(
    rf"(?<![0-9a-fA-F])([0-9a-fA-F]{{48,{_MAX_HEX_TOKEN_CHARS}}})(?![0-9a-fA-F])"
)


def _byte_entropy(data: bytes) -> float:
    """Compute Shannon entropy of *data* in bits per byte."""
    if not data:
        return 0.0
    total = len(data)
    counts: dict[int, int] = {}
    for b in data:
        counts[b] = counts.get(b, 0) + 1
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def _decode_as_text(raw: bytes) -> str | None:
    """Return *raw* decoded as UTF-8 text if it looks human-readable, else ``None``.

    Rejects payloads that are:
    - Larger than ``_MAX_DECODED_BYTES`` (DoS guard).
    - Not valid UTF-8 (binary blobs: images, crypto keys, DER certs).
    - Shannon entropy > ``_MAX_TEXT_ENTROPY_BITS`` (compressed or encrypted data).
    """
    if len(raw) > _MAX_DECODED_BYTES:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if _byte_entropy(raw) > _MAX_TEXT_ENTROPY_BITS:
        return None
    return text


def _has_encoded_injection(
    content: str,
    patterns: list[tuple[str, re.Pattern[str]]],
) -> bool:
    """Return ``True`` if *content* contains an encoded injection payload.

    Scans for standalone standard-base64, url-safe-base64, and hex tokens,
    decodes each candidate (with size + entropy gates), then re-checks the
    full injection *patterns* set on the decoded text.
    """

    def _matches_any(text: str) -> bool:
        return any(pat.search(text) for _, pat in patterns)

    # --- Standard base64 ---
    for m in _B64_TOKEN_RE.finditer(content):
        token = m.group(1)
        pad = (4 - len(token) % 4) % 4
        try:
            raw = base64.b64decode(token + "=" * pad, validate=True)
        except Exception:
            continue
        text = _decode_as_text(raw)
        if text is not None and _matches_any(text):
            return True

    # --- URL-safe base64 (only tokens that use - or _; others caught above) ---
    for m in _URLSAFE_B64_TOKEN_RE.finditer(content):
        token = m.group(1)
        if "-" not in token and "_" not in token:
            continue
        pad = (4 - len(token) % 4) % 4
        try:
            raw = base64.urlsafe_b64decode(token + "=" * pad)
        except Exception:
            continue
        text = _decode_as_text(raw)
        if text is not None and _matches_any(text):
            return True

    # --- Hex ---
    for m in _HEX_TOKEN_RE.finditer(content):
        try:
            raw = bytes.fromhex(m.group(1))
        except ValueError:
            continue
        text = _decode_as_text(raw)
        if text is not None and _matches_any(text):
            return True

    return False


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

    # Encoded-payload check (base64 / url-safe-b64 / hex).
    # Obfuscated injections are always blocked — deliberate encoding is a
    # strong threat signal regardless of plaintext match count / density.
    if _has_encoded_injection(normalised, patterns):
        encoded_flagged = [*flagged, "base64_payload"]
        encoded_total = total_matches + 1
        logger.warning(
            "rag_safety_encoded_injection_blocked",
            match_count=encoded_total,
            patterns=encoded_flagged,
            ruleset_version=resolved,
        )
        result = SafetyCheckResult(
            safe=False,
            flagged_patterns=encoded_flagged,
            match_count=encoded_total,
            warning=(
                f"Content blocked: encoded injection payload detected "
                f"({', '.join(encoded_flagged)})"
            ),
            ruleset_version=resolved,
        )
        if metrics is not None:
            metrics.increment("safety.encoded_injection_blocked_total")
            metrics.increment("rag_safety.blocked")
        return result

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

    # Low match count - sanitise and warn. Prefer the original bytes so
    # benign Unicode (ligatures, curly quotes, CJK compatibility chars)
    # survives (TAP-712). Homograph/lookalike injections only match after
    # NFKC, so if the original-byte pass left patterns intact, redact the
    # normalised form instead — never claim "sanitised" while the payload
    # still matches after NFKC.
    sanitised = _sanitise_content(content, patterns)
    if any(pattern.search(unicodedata.normalize("NFKC", sanitised)) for _, pattern in patterns):
        sanitised = _sanitise_content(normalised, patterns)
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
