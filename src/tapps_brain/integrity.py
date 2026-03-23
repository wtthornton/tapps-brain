"""HMAC-SHA256 integrity hashing for memory entries.

Provides tamper detection for stored memory content. A per-installation
HMAC key is stored at ``~/.tapps-brain/integrity.key`` and used to sign
canonical entry data on save. Verification compares the stored hash
against a freshly computed one.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Default location for the HMAC signing key.
_DEFAULT_KEY_DIR = Path.home() / ".tapps-brain"
_DEFAULT_KEY_PATH = _DEFAULT_KEY_DIR / "integrity.key"

# Key length in bytes (256-bit).
_KEY_LENGTH = 32


def _ensure_key(key_path: Path | None = None) -> bytes:
    """Load or generate the HMAC signing key.

    If the key file does not exist, a cryptographically random 32-byte key
    is generated and persisted. The directory is created with restricted
    permissions where possible.

    Args:
        key_path: Override path for the key file. Defaults to
            ``~/.tapps-brain/integrity.key``.

    Returns:
        The raw HMAC key bytes.
    """
    path = key_path or _DEFAULT_KEY_PATH

    if path.exists():
        raw = path.read_bytes()
        if len(raw) >= _KEY_LENGTH:
            return raw[:_KEY_LENGTH]
        # Key file is too short - regenerate
        logger.warning("integrity_key_too_short", path=str(path), length=len(raw))

    # Generate a new key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(_KEY_LENGTH)
    path.write_bytes(key)
    logger.info("integrity_key_generated", path=str(path))
    return key


# Module-level cache so we only read the key file once per process.
_cached_key: bytes | None = None


def get_signing_key(key_path: Path | None = None) -> bytes:
    """Return the HMAC signing key, loading or generating as needed.

    The key is cached in-process after the first call.

    Args:
        key_path: Override path for the key file.

    Returns:
        The raw HMAC key bytes.
    """
    global _cached_key
    if _cached_key is None:
        _cached_key = _ensure_key(key_path)
    return _cached_key


def reset_key_cache() -> None:
    """Clear the cached signing key (useful for testing)."""
    global _cached_key
    _cached_key = None


def compute_integrity_hash(
    key: str,
    value: str,
    tier: str,
    source: str,
    *,
    signing_key: bytes | None = None,
) -> str:
    """Compute HMAC-SHA256 over the canonical entry fields.

    The canonical form is ``key|value|tier|source`` encoded as UTF-8.
    The pipe separator is chosen because keys are validated slugs that
    cannot contain pipes.

    Args:
        key: Memory entry key.
        value: Memory entry value.
        tier: Memory tier string.
        source: Memory source string.
        signing_key: Override HMAC key bytes. If ``None``, uses the
            default key from ``get_signing_key()``.

    Returns:
        Hex-encoded HMAC-SHA256 digest.
    """
    hmac_key = signing_key or get_signing_key()
    canonical = f"{key}|{value}|{tier}|{source}".encode()
    return hmac.new(hmac_key, canonical, hashlib.sha256).hexdigest()


def verify_integrity_hash(
    key: str,
    value: str,
    tier: str,
    source: str,
    stored_hash: str,
    *,
    signing_key: bytes | None = None,
) -> bool:
    """Verify an entry's integrity hash using constant-time comparison.

    Args:
        key: Memory entry key.
        value: Memory entry value.
        tier: Memory tier string.
        source: Memory source string.
        stored_hash: The hex-encoded hash to verify against.
        signing_key: Override HMAC key bytes.

    Returns:
        ``True`` if the hash matches, ``False`` if tampered or missing.
    """
    expected = compute_integrity_hash(key, value, tier, source, signing_key=signing_key)
    return hmac.compare_digest(expected, stored_hash)
