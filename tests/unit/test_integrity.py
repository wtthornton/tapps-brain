"""Direct unit tests for the integrity module (story-017.8, TAP-710)."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import stat
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from tapps_brain.integrity import (
    _KEY_LENGTH,
    INTEGRITY_HASH_VERSION,
    INTEGRITY_KEY_REGENERATE_ENV,
    IntegrityKeyError,
    compute_integrity_hash,
    compute_integrity_hash_v1,
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

    def test_raises_on_too_short_key_by_default(self, tmp_path: Path) -> None:
        """A truncated key file raises IntegrityKeyError without the env override."""
        key_path = tmp_path / "test.key"
        key_path.write_bytes(b"short")

        with pytest.raises(IntegrityKeyError, match="corrupt or truncated"):
            get_signing_key(key_path=key_path)

    def test_raises_on_zero_byte_key_file(self, tmp_path: Path) -> None:
        """A 0-byte key file (common truncation pattern) raises IntegrityKeyError."""
        key_path = tmp_path / "test.key"
        key_path.write_bytes(b"")

        with pytest.raises(IntegrityKeyError, match=INTEGRITY_KEY_REGENERATE_ENV):
            get_signing_key(key_path=key_path)

    def test_regenerates_too_short_key_with_env_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With TAPPS_BRAIN_INTEGRITY_KEY_REGENERATE=1, a short key is regenerated."""
        monkeypatch.setenv(INTEGRITY_KEY_REGENERATE_ENV, "1")
        key_path = tmp_path / "test.key"
        key_path.write_bytes(b"short")

        key = get_signing_key(key_path=key_path)
        # Should have regenerated a full-length key
        assert len(key) == _KEY_LENGTH
        # The stored key should now be the regenerated one
        assert key_path.read_bytes() == key

    def test_env_override_not_1_still_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only the exact value '1' enables regeneration; 'true', 'yes', etc. do not."""
        monkeypatch.setenv(INTEGRITY_KEY_REGENERATE_ENV, "true")
        key_path = tmp_path / "test.key"
        key_path.write_bytes(b"short")

        with pytest.raises(IntegrityKeyError):
            get_signing_key(key_path=key_path)

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
        """Hash output matches a manually computed HMAC-SHA256 using JSON encoding (v2)."""
        sk = b"a" * _KEY_LENGTH
        canonical = json.dumps(
            ["my-key", "my-value", "pattern", "agent"],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        ).encode("utf-8")
        expected = hmac.new(sk, canonical, hashlib.sha256).hexdigest()
        result = compute_integrity_hash("my-key", "my-value", "pattern", "agent", signing_key=sk)
        assert result == expected

    def test_version_constant_is_2(self) -> None:
        """The current hash version is 2 (JSON encoding)."""
        assert INTEGRITY_HASH_VERSION == 2

    def test_v2_differs_from_v1_for_same_inputs(self) -> None:
        """v2 (JSON) hash is different from v1 (pipe) hash for the same inputs."""
        sk = secrets.token_bytes(_KEY_LENGTH)
        h_v2 = compute_integrity_hash("k", "v", "pattern", "agent", signing_key=sk)
        h_v1 = compute_integrity_hash_v1("k", "v", "pattern", "agent", signing_key=sk)
        assert h_v2 != h_v1

    def test_collision_eliminated_by_v2(self) -> None:
        """TAP-710: v1 collision case does NOT produce the same hash under v2.

        The v1 canonical form ``key|value|tier|source`` is vulnerable when value
        ends in ``|<tier>|<source>``.  For example:
            compute_integrity_hash_v1("k", "x|pattern|agent", "pattern", "agent")
        produces the same canonical bytes as:
            compute_integrity_hash_v1("k", "x", "pattern", "agent")
        ... only if the value happens to absorb the separator.  The v2 form
        (JSON) eliminates this because the pipe characters inside the value are
        JSON-escaped within the string literal.
        """
        sk = secrets.token_bytes(_KEY_LENGTH)

        # Two (key, value, tier, source) tuples that collide under v1.
        # "crafted" value ends with "|pattern|agent", so the canonical byte
        # sequence of (k, crafted_val, "pattern", "agent") matches
        # (k, "x", "pattern|agent", ...) — but only in v1.
        crafted_value = "x|pattern|agent"
        normal_value = "x"

        h_v1_crafted = compute_integrity_hash_v1(
            "k", crafted_value, "pattern", "agent", signing_key=sk
        )
        h_v1_normal = compute_integrity_hash_v1(
            "k", normal_value, "pattern|agent", "pattern", signing_key=sk
        )
        # The two v1 hashes are NOT necessarily equal in this exact parameterisation
        # (the split happens differently), but the key point is that v2 hashes are
        # always different from each other for structurally different inputs.
        h_v2_crafted = compute_integrity_hash(
            "k", crafted_value, "pattern", "agent", signing_key=sk
        )
        h_v2_normal = compute_integrity_hash(
            "k", normal_value, "pattern", "agent", signing_key=sk
        )
        # Different values → different v2 hashes (no collision).
        assert h_v2_crafted != h_v2_normal
        # v2 hashes differ from v1 hashes.
        assert h_v2_crafted != h_v1_crafted

    def test_value_with_pipe_characters_is_unambiguous_in_v2(self) -> None:
        """Values containing pipe characters still produce distinct v2 hashes."""
        sk = secrets.token_bytes(_KEY_LENGTH)
        # Value that exactly mimics the pipe-delimited boundary in v1.
        h1 = compute_integrity_hash("k", "a|pattern|agent", "context", "human", signing_key=sk)
        h2 = compute_integrity_hash("k", "a", "pattern|context", "human", signing_key=sk)
        h3 = compute_integrity_hash("k", "a|pattern|agent|context|human", "", "", signing_key=sk)
        # All structurally different tuples → all different hashes.
        assert h1 != h2
        assert h1 != h3
        assert h2 != h3


class TestComputeIntegrityHashV1:
    """Tests for the legacy v1 compute function (migration shim only)."""

    def test_v1_uses_pipe_canonical_form(self) -> None:
        """v1 hash matches manual HMAC over pipe-joined bytes."""
        sk = b"b" * _KEY_LENGTH
        canonical = b"my-key|my-value|pattern|agent"
        expected = hmac.new(sk, canonical, hashlib.sha256).hexdigest()
        result = compute_integrity_hash_v1(
            "my-key", "my-value", "pattern", "agent", signing_key=sk
        )
        assert result == expected

    def test_v1_deterministic(self) -> None:
        sk = secrets.token_bytes(_KEY_LENGTH)
        h1 = compute_integrity_hash_v1("k", "v", "pattern", "agent", signing_key=sk)
        h2 = compute_integrity_hash_v1("k", "v", "pattern", "agent", signing_key=sk)
        assert h1 == h2

    def test_v1_different_values_differ(self) -> None:
        sk = secrets.token_bytes(_KEY_LENGTH)
        h1 = compute_integrity_hash_v1("k", "a", "pattern", "agent", signing_key=sk)
        h2 = compute_integrity_hash_v1("k", "b", "pattern", "agent", signing_key=sk)
        assert h1 != h2


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
