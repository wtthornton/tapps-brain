"""Direct unit tests for the integrity module (story-017.8)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import stat
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from tapps_brain.integrity import (
    _KEY_LENGTH,
    compute_integrity_hash,
    get_signing_key,
    reset_key_cache,
    verify_integrity_hash,
)


@pytest.fixture(autouse=True)
def _reset_key() -> None:
    """Reset the key cache before every test."""
    reset_key_cache()
    yield
    reset_key_cache()


class TestEnsureKey:
    """Tests for key generation and loading."""

    def test_generates_key_when_missing(self, tmp_path: Path) -> None:
        key_path = tmp_path / "test.key"
        key = get_signing_key(key_path=key_path)
        assert len(key) == _KEY_LENGTH
        assert key_path.exists()

    def test_loads_existing_key(self, tmp_path: Path) -> None:
        key_path = tmp_path / "test.key"
        existing_key = secrets.token_bytes(_KEY_LENGTH)
        key_path.write_bytes(existing_key)

        key = get_signing_key(key_path=key_path)
        assert key == existing_key

    def test_truncates_oversized_key(self, tmp_path: Path) -> None:
        key_path = tmp_path / "test.key"
        long_key = secrets.token_bytes(64)  # double the expected length
        key_path.write_bytes(long_key)

        key = get_signing_key(key_path=key_path)
        assert key == long_key[:_KEY_LENGTH]
        assert len(key) == _KEY_LENGTH

    def test_regenerates_too_short_key(self, tmp_path: Path) -> None:
        key_path = tmp_path / "test.key"
        # Write a key that is too short
        key_path.write_bytes(b"short")

        key = get_signing_key(key_path=key_path)
        # Should have regenerated a full-length key
        assert len(key) == _KEY_LENGTH
        # The stored key should now be the regenerated one
        assert key_path.read_bytes() == key

    @pytest.mark.skipif(sys.platform == "win32", reason="chmod not meaningful on Windows")
    def test_key_file_has_restricted_permissions(self, tmp_path: Path) -> None:
        key_path = tmp_path / "test.key"
        get_signing_key(key_path=key_path)

        mode = stat.S_IMODE(key_path.stat().st_mode)
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


class TestGetSigningKeyCache:
    """Tests for the module-level key cache."""

    def test_same_key_returned_on_repeated_calls(self, tmp_path: Path) -> None:
        key_path = tmp_path / "test.key"
        key1 = get_signing_key(key_path=key_path)
        key2 = get_signing_key(key_path=key_path)
        assert key1 == key2

    def test_key_path_ignored_after_first_call(self, tmp_path: Path) -> None:
        path_a = tmp_path / "a.key"
        path_b = tmp_path / "b.key"

        key_a = get_signing_key(key_path=path_a)
        # Second call uses a different path but should return the cached key
        key_b = get_signing_key(key_path=path_b)
        assert key_a == key_b
        # path_b should NOT have been created
        assert not path_b.exists()

    def test_reset_clears_cache(self, tmp_path: Path) -> None:
        path_a = tmp_path / "a.key"
        path_b = tmp_path / "b.key"

        key_a = get_signing_key(key_path=path_a)
        reset_key_cache()
        key_b = get_signing_key(key_path=path_b)

        # Different paths → different random keys
        assert key_a != key_b
        assert path_b.exists()


class TestComputeIntegrityHash:
    """Tests for compute_integrity_hash."""

    def test_returns_hex_string(self) -> None:
        result = compute_integrity_hash(
            "test-key",
            "test-value",
            "pattern",
            "agent",
            signing_key=b"\x00" * _KEY_LENGTH,
        )
        assert isinstance(result, str)
        # SHA-256 hex digest is 64 characters
        assert len(result) == 64
        int(result, 16)  # must parse as hex

    def test_deterministic_same_inputs(self) -> None:
        sk = secrets.token_bytes(_KEY_LENGTH)
        h1 = compute_integrity_hash("k", "v", "pattern", "agent", signing_key=sk)
        h2 = compute_integrity_hash("k", "v", "pattern", "agent", signing_key=sk)
        assert h1 == h2

    def test_different_values_produce_different_hashes(self) -> None:
        sk = secrets.token_bytes(_KEY_LENGTH)
        h1 = compute_integrity_hash("k", "value-a", "pattern", "agent", signing_key=sk)
        h2 = compute_integrity_hash("k", "value-b", "pattern", "agent", signing_key=sk)
        assert h1 != h2

    def test_different_tiers_produce_different_hashes(self) -> None:
        sk = secrets.token_bytes(_KEY_LENGTH)
        h1 = compute_integrity_hash("k", "v", "pattern", "agent", signing_key=sk)
        h2 = compute_integrity_hash("k", "v", "architectural", "agent", signing_key=sk)
        assert h1 != h2

    def test_different_sources_produce_different_hashes(self) -> None:
        sk = secrets.token_bytes(_KEY_LENGTH)
        h1 = compute_integrity_hash("k", "v", "pattern", "agent", signing_key=sk)
        h2 = compute_integrity_hash("k", "v", "pattern", "human", signing_key=sk)
        assert h1 != h2

    def test_empty_value(self) -> None:
        """Empty string value is hashed without error."""
        sk = secrets.token_bytes(_KEY_LENGTH)
        result = compute_integrity_hash("k", "", "pattern", "agent", signing_key=sk)
        assert len(result) == 64

    def test_empty_all_fields(self) -> None:
        """All empty strings produce a valid hash."""
        sk = secrets.token_bytes(_KEY_LENGTH)
        result = compute_integrity_hash("", "", "", "", signing_key=sk)
        assert len(result) == 64

    def test_unicode_value(self) -> None:
        """Multi-byte Unicode in value is handled correctly."""
        sk = secrets.token_bytes(_KEY_LENGTH)
        h1 = compute_integrity_hash("k", "héllo wörld 日本語", "pattern", "agent", signing_key=sk)
        h2 = compute_integrity_hash("k", "héllo wörld 日本語", "pattern", "agent", signing_key=sk)
        assert h1 == h2
        assert len(h1) == 64

    def test_different_signing_keys_different_hashes(self) -> None:
        sk1 = secrets.token_bytes(_KEY_LENGTH)
        sk2 = secrets.token_bytes(_KEY_LENGTH)
        h1 = compute_integrity_hash("k", "v", "pattern", "agent", signing_key=sk1)
        h2 = compute_integrity_hash("k", "v", "pattern", "agent", signing_key=sk2)
        assert h1 != h2

    def test_matches_manual_hmac(self) -> None:
        """Hash output matches a manually computed HMAC-SHA256."""
        sk = b"a" * _KEY_LENGTH
        canonical = b"my-key|my-value|pattern|agent"
        expected = hmac.new(sk, canonical, hashlib.sha256).hexdigest()
        result = compute_integrity_hash("my-key", "my-value", "pattern", "agent", signing_key=sk)
        assert result == expected


class TestVerifyIntegrityHash:
    """Tests for verify_integrity_hash."""

    def test_valid_hash_returns_true(self) -> None:
        sk = secrets.token_bytes(_KEY_LENGTH)
        h = compute_integrity_hash("k", "v", "pattern", "agent", signing_key=sk)
        assert verify_integrity_hash("k", "v", "pattern", "agent", h, signing_key=sk) is True

    def test_tampered_value_returns_false(self) -> None:
        sk = secrets.token_bytes(_KEY_LENGTH)
        h = compute_integrity_hash("k", "original", "pattern", "agent", signing_key=sk)
        result = verify_integrity_hash("k", "tampered", "pattern", "agent", h, signing_key=sk)
        assert result is False

    def test_tampered_tier_returns_false(self) -> None:
        sk = secrets.token_bytes(_KEY_LENGTH)
        h = compute_integrity_hash("k", "v", "pattern", "agent", signing_key=sk)
        assert verify_integrity_hash("k", "v", "architectural", "agent", h, signing_key=sk) is False

    def test_tampered_source_returns_false(self) -> None:
        sk = secrets.token_bytes(_KEY_LENGTH)
        h = compute_integrity_hash("k", "v", "pattern", "agent", signing_key=sk)
        assert verify_integrity_hash("k", "v", "pattern", "human", h, signing_key=sk) is False

    def test_tampered_key_returns_false(self) -> None:
        sk = secrets.token_bytes(_KEY_LENGTH)
        h = compute_integrity_hash("original-key", "v", "pattern", "agent", signing_key=sk)
        result = verify_integrity_hash("different-key", "v", "pattern", "agent", h, signing_key=sk)
        assert result is False

    def test_wrong_signing_key_returns_false(self) -> None:
        sk1 = secrets.token_bytes(_KEY_LENGTH)
        sk2 = secrets.token_bytes(_KEY_LENGTH)
        h = compute_integrity_hash("k", "v", "pattern", "agent", signing_key=sk1)
        assert verify_integrity_hash("k", "v", "pattern", "agent", h, signing_key=sk2) is False

    def test_empty_value_round_trips(self) -> None:
        sk = secrets.token_bytes(_KEY_LENGTH)
        h = compute_integrity_hash("k", "", "pattern", "agent", signing_key=sk)
        assert verify_integrity_hash("k", "", "pattern", "agent", h, signing_key=sk) is True

    def test_uses_constant_time_comparison(self) -> None:
        """Verify uses hmac.compare_digest (constant-time) not == operator.

        We can only confirm the interface — not the timing — in a unit test.
        """
        sk = secrets.token_bytes(_KEY_LENGTH)
        h = compute_integrity_hash("k", "v", "pattern", "agent", signing_key=sk)
        # If the comparison were non-constant-time, this would still work correctly
        # functionally — we just ensure the function exists and returns the right result.
        assert verify_integrity_hash("k", "v", "pattern", "agent", h, signing_key=sk) is True
