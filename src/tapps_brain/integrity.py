"""HMAC-SHA256 integrity hashing for memory entries.

Provides tamper detection for stored memory content. A per-installation
HMAC key is stored at ``~/.tapps-brain/integrity.key`` and used to sign
canonical entry data on save. Verification compares the stored hash
against a freshly computed one.

Key-file safety
---------------
If the key file exists but is corrupt or truncated, :func:`_ensure_key`
**raises** :class:`IntegrityKeyError` rather than silently regenerating the
key.  Silent regeneration destroys the tamper-audit trail and lets an
attacker truncate the file to force key rotation.

To intentionally regenerate the key (e.g. after a documented key-rotation
procedure), set the environment variable::

    TAPPS_BRAIN_INTEGRITY_KEY_REGENERATE=1

This escape hatch emits a structured ERROR log and invalidates all existing
integrity hashes — use it only with explicit operator intent.

Hash versioning
---------------
:data:`INTEGRITY_HASH_VERSION` tracks the canonical encoding scheme:

* **v1** (legacy) — ``key|value|tier|source`` UTF-8 pipe-joined string.
  Vulnerable to collision when ``value`` contains a literal ``|`` followed by
  a valid ``tier|source`` suffix.  Present in entries written before
  TAP-710 was addressed.
* **v2** (current) — ``json.dumps([key, value, tier, source], ...)`` UTF-8.
  JSON encoding is unambiguous: no field can produce a false boundary
  because JSON string escaping prevents literal ``"`` characters from being
  confused with delimiters.

Use :func:`compute_integrity_hash` (v2) for all new writes.  Use
:func:`compute_integrity_hash_v1` only inside the migration shim that
upgrades existing rows from v1 → v2.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Default location for the HMAC signing key.
_DEFAULT_KEY_DIR = Path.home() / ".tapps-brain"
_DEFAULT_KEY_PATH = _DEFAULT_KEY_DIR / "integrity.key"

# Key length in bytes (256-bit).
_KEY_LENGTH = 32

#: Environment variable that opts in to forced key regeneration when the key
#: file exists but is truncated or corrupt.  Set to ``"1"`` to enable.
INTEGRITY_KEY_REGENERATE_ENV = "TAPPS_BRAIN_INTEGRITY_KEY_REGENERATE"

#: TAP-784: environment variable for injecting the HMAC key without writing
#: it to disk.  Accepts a base64-encoded or hex-encoded 32-byte key.  When
#: set, ``_ensure_key`` returns the decoded value and skips all file I/O so
#: the key never touches disk.  Suitable for Vault, k8s Secret env injection,
#: or any secrets manager that can surface secrets as environment variables.
#:
#: Generate a key::
#:
#:     python -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
INTEGRITY_KEY_ENV = "TAPPS_BRAIN_INTEGRITY_KEY"


class IntegrityKeyEnvError(RuntimeError):
    """Raised when ``TAPPS_BRAIN_INTEGRITY_KEY`` is set but cannot be decoded."""


#: Current canonical encoding version used by :func:`compute_integrity_hash`.
#: v1 = legacy pipe-joined (TAP-710); v2 = JSON array (current).
INTEGRITY_HASH_VERSION: int = 2


class IntegrityKeyError(RuntimeError):
    """Raised when the HMAC signing key file is corrupt, truncated, or missing.

    This error requires explicit operator intervention.  Do not catch and
    suppress it silently — it indicates potential tampering or disk corruption.
    To force regeneration (which invalidates all existing hashes), set the
    environment variable ``TAPPS_BRAIN_INTEGRITY_KEY_REGENERATE=1``.
    """


def _decode_env_key(value: str) -> bytes:
    """Decode a base64 or hex-encoded key from ``TAPPS_BRAIN_INTEGRITY_KEY``.

    Detection order:
    1. Hex — exactly 64 lowercase/uppercase hex characters (32 bytes).
    2. Base64 (standard or URL-safe) — all other values.

    Hex is detected first because hex chars are a valid subset of the base64
    alphabet, so base64 decoding would silently produce wrong bytes.

    Raises:
        IntegrityKeyEnvError: when the value cannot be decoded or decodes to
            fewer than ``_KEY_LENGTH`` bytes.
    """
    # 1. Hex: 64-char string consisting solely of hex digits encodes 32 bytes.
    stripped = value.strip()
    if len(stripped) == _KEY_LENGTH * 2 and all(c in "0123456789abcdefABCDEF" for c in stripped):
        try:
            raw = bytes.fromhex(stripped)
            if len(raw) >= _KEY_LENGTH:
                return raw[:_KEY_LENGTH]
        except Exception:
            pass

    # 2. Base64 (standard then URL-safe).
    for decode in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            raw = decode(stripped + "==")  # pad defensively
            if len(raw) >= _KEY_LENGTH:
                return raw[:_KEY_LENGTH]
        except Exception:
            pass

    raise IntegrityKeyEnvError(
        f"{INTEGRITY_KEY_ENV} is set but could not be decoded as base64 or hex, "
        f"or decoded to fewer than {_KEY_LENGTH} bytes. "
        'Generate a key with: python -c "import secrets,base64; '
        'print(base64.b64encode(secrets.token_bytes(32)).decode())"'
    )


def _ensure_key(key_path: Path | None = None) -> bytes:
    """Load or generate the HMAC signing key.

    If the key file does not exist (first boot), a cryptographically random
    32-byte key is generated and persisted.  The directory is created with
    restricted permissions where possible.

    If the key file **exists but is too short** (truncated or corrupt), this
    function raises :class:`IntegrityKeyError` instead of silently overwriting
    the file.  Set ``TAPPS_BRAIN_INTEGRITY_KEY_REGENERATE=1`` in the
    environment to override this and force regeneration (emits a structured
    ERROR log and invalidates all existing integrity hashes).

    Args:
        key_path: Override path for the key file. Defaults to
            ``~/.tapps-brain/integrity.key``.

    Returns:
        The raw HMAC key bytes.

    Raises:
        IntegrityKeyError: If the key file is present but corrupt/truncated
            and ``TAPPS_BRAIN_INTEGRITY_KEY_REGENERATE`` is not set to ``"1"``.
        IntegrityKeyEnvError: If ``TAPPS_BRAIN_INTEGRITY_KEY`` is set but
            cannot be decoded as base64 or hex.
    """
    # TAP-784: env var injection takes priority over disk — the key never
    # touches the filesystem, which is required when using external secrets
    # managers (Vault, k8s Secrets, AWS Secrets Manager, etc.).
    env_key = os.environ.get(INTEGRITY_KEY_ENV, "")
    if env_key:
        raw = _decode_env_key(env_key)
        logger.info("integrity_key_loaded_from_env")
        return raw

    path = key_path or _DEFAULT_KEY_PATH

    if path.exists():
        raw = path.read_bytes()
        if len(raw) >= _KEY_LENGTH:
            return raw[:_KEY_LENGTH]

        # Key file exists but is too short — truncated or corrupt.
        allow_regen = os.environ.get(INTEGRITY_KEY_REGENERATE_ENV, "0") == "1"
        if not allow_regen:
            raise IntegrityKeyError(
                f"Integrity key file is corrupt or truncated "
                f"(expected {_KEY_LENGTH} bytes, got {len(raw)}): {path}. "
                f"This may indicate tampering or disk corruption. "
                f"All existing integrity hashes would be invalidated by regeneration. "
                f"To force regeneration, set {INTEGRITY_KEY_REGENERATE_ENV}=1 "
                f"(this is a destructive, operator-gated action)."
            )

        # Forced regeneration — operator opted in explicitly.
        logger.error(
            "integrity_key_too_short_regenerating",
            path=str(path),
            length=len(raw),
            warning="all_existing_integrity_hashes_invalidated",
            hint=f"unset {INTEGRITY_KEY_REGENERATE_ENV} after key rotation is complete",
        )

    # Generate a new key (first-boot or operator-forced regeneration).
    #
    # Directory: create with mode=0o700 then explicitly chmod to override umask.
    # Without the explicit chmod, mkdir with mode=0o700 still applies the
    # caller's umask (e.g. umask 022 → 0o755, world-readable).
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        logger.warning("integrity_key_dir_chmod_failed", path=str(path.parent))

    key = secrets.token_bytes(_KEY_LENGTH)

    # Write atomically via a temp file in the same directory.
    # This eliminates the chmod-after-write race: the file is created with
    # restricted permissions from the outset, and os.replace() makes the key
    # visible to readers only once the write is complete.
    # On POSIX, os.fchmod bypasses umask to set the exact mode on the fd before
    # any data is written.  On Windows, ACL enforcement is the storage layer's
    # responsibility (use a secrets vault or encrypted volume).
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".integrity-")
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(tmp_fd, 0o600)
        os.write(tmp_fd, key)
    finally:
        os.close(tmp_fd)
    try:
        os.replace(tmp_name, str(path))
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise

    # TAP-784: WARNING (not INFO) so operators can detect unexpected key
    # generation in production — a new key invalidates all existing hashes.
    logger.warning("integrity_key_generated", path=str(path))
    return key


# Module-level cache so we only read the key file once per process.
_cached_key: bytes | None = None


def get_signing_key(key_path: Path | None = None) -> bytes:
    """Return the HMAC signing key, loading or generating as needed.

    The key is cached in-process after the first call to avoid repeated
    file I/O.  The ``key_path`` parameter is **only honoured on the first
    call** — subsequent calls with a different path return the already-cached
    key.  Use :func:`reset_key_cache` before calling with a new path (e.g.
    in tests).

    Args:
        key_path: Override path for the key file.  Only used on the first
            call in each process (or after :func:`reset_key_cache`).

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
    """Compute HMAC-SHA256 over the canonical entry fields (v2 — JSON encoding).

    The canonical form is a JSON array ``[key, value, tier, source]`` serialised
    as a UTF-8 byte string with no trailing whitespace.  JSON encoding eliminates
    the field-boundary collision present in the legacy pipe-joined form (TAP-710):
    because JSON string values are always ``"``-delimited and internally escaped,
    no combination of field contents can produce a false field boundary.

    This function always produces v2 hashes.  Use :func:`compute_integrity_hash_v1`
    only inside the one-time migration shim that identifies rows still carrying
    legacy v1 hashes.

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
    hmac_key = signing_key if signing_key is not None else get_signing_key()
    canonical = json.dumps(
        [key, value, tier, source],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    ).encode("utf-8")
    return hmac.new(hmac_key, canonical, hashlib.sha256).hexdigest()


def compute_integrity_hash_v1(
    key: str,
    value: str,
    tier: str,
    source: str,
    *,
    signing_key: bytes | None = None,
) -> str:
    """Compute a **legacy v1** HMAC-SHA256 hash using the pipe-joined canonical form.

    .. deprecated::
        This function exists *only* to support the v1 → v2 migration shim.
        Do **not** use it for new writes.  Call :func:`compute_integrity_hash`
        (v2) instead.

    The v1 canonical form is ``key|value|tier|source`` encoded as UTF-8.
    It is vulnerable to collision when ``value`` contains a literal ``|``
    followed by a valid ``tier|source`` suffix (TAP-710).

    Args:
        key: Memory entry key.
        value: Memory entry value.
        tier: Memory tier string.
        source: Memory source string.
        signing_key: Override HMAC key bytes. If ``None``, uses the
            default key from ``get_signing_key()``.

    Returns:
        Hex-encoded HMAC-SHA256 digest computed with the v1 scheme.
    """
    hmac_key = signing_key if signing_key is not None else get_signing_key()
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
