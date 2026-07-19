"""Integrity-hash verification for :class:`~tapps_brain.store.MemoryStore` (TAP-2833).

Extracted from ``store.py`` as a mixin.  Covers HMAC integrity verification
(H4b) and the v1->v2 rehash migration shim (TAP-710).

TAP-2857: the upgrade branch of :meth:`rehash_integrity_v1` previously referenced
a non-existent ``self._backend`` attribute (raising ``AttributeError`` and never
persisting the rehash); it now persists via ``self._persistence``.
"""

from __future__ import annotations

import hmac
from typing import Any

import structlog

from tapps_brain._store_base import _MemoryStoreBase
from tapps_brain.integrity import (
    INTEGRITY_HASH_VERSION,
    compute_integrity_hash,
    compute_integrity_hash_v1,
    verify_integrity_hash,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

#: TAP-4331: fraction of hashed entries that must fail verification before we call
#: it a signing-key mismatch (vs selective tampering). High enough that a few
#: correctly-signed rows alongside a restored-under-a-different-key bulk still
#: reads as a key mismatch.
_KEY_MISMATCH_RATIO = 0.95


def _hash_field_strs(entry: Any) -> tuple[str, str]:  # noqa: ANN401 — MemoryEntry duck-typed
    """Return ``(tier_str, source_str)`` in the canonical form used for hashing.

    Tier/source may be enums or raw strings (profile layers); the integrity
    hash always uses the enum ``.value`` when present.  Shared by all three
    integrity methods so the canonical form cannot drift.
    """
    tier_str = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
    source_str = entry.source.value if hasattr(entry.source, "value") else str(entry.source)
    return tier_str, source_str


class IntegrityMixin(_MemoryStoreBase):
    """HMAC integrity verification + v1->v2 rehash shim (TAP-2833)."""

    def verify_integrity(self) -> dict[str, Any]:
        """Scan all entries and verify their HMAC integrity hashes.

        For each entry that has a stored ``integrity_hash``, recomputes the
        HMAC-SHA256 and checks for a match. Entries without a stored hash
        (pre-v8 or NULL) are reported separately.

        Returns:
            Dict with ``total``, ``verified``, ``tampered``, ``no_hash``,
            ``tampered_keys``, ``missing_hash_keys``, ``tampered_details``.
        """
        self._metrics.increment("store.verify_integrity")

        # Include durable overflow beyond the cold-start cache cap.
        self._merge_durable_entries()
        with self._serialized():
            entries = list(self._entries.values())

        total = len(entries)
        verified = 0
        tampered: list[str] = []
        tampered_details: list[dict[str, str]] = []
        missing_hash_keys: list[str] = []

        for entry in entries:
            stored_hash = getattr(entry, "integrity_hash", None)
            if not stored_hash:
                missing_hash_keys.append(entry.key)
                continue

            tier_str, source_str = _hash_field_strs(entry)
            hash_version = getattr(entry, "integrity_hash_v", 1)

            # Use the version-appropriate verifier so legacy v1 rows don't
            # spuriously show as tampered when the process uses the v2 scheme.
            if hash_version == 1:
                # v1: legacy pipe-joined canonical form
                v1_expected = compute_integrity_hash_v1(
                    entry.key, entry.value, tier_str, source_str
                )
                if hmac.compare_digest(v1_expected, stored_hash):
                    verified += 1
                    continue
            elif verify_integrity_hash(entry.key, entry.value, tier_str, source_str, stored_hash):
                verified += 1
                continue

            tampered.append(entry.key)
            expected = compute_integrity_hash(entry.key, entry.value, tier_str, source_str)
            tampered_details.append(
                {
                    "key": entry.key,
                    "stored_hash": stored_hash,
                    "expected_hash": expected,
                    "hash_version": str(hash_version),
                }
            )

        # TAP-4331: when (almost) every hashed entry fails verification, the cause
        # is overwhelmingly a signing-key mismatch (e.g. data restored under a
        # different ~/.tapps-brain/integrity.key), not selective tampering. Use a
        # ratio so a handful of freshly, correctly-signed rows don't flip a
        # wholesale key mismatch back to a "tampered" verdict.
        _hashed = verified + len(tampered)
        likely_key_mismatch = _hashed > 0 and (len(tampered) / _hashed) >= _KEY_MISMATCH_RATIO

        # TAP-4331: emit ONE aggregated summary instead of a per-entry warning
        # storm (5000 rows -> 5000 log lines on every store load drowned out
        # real signal).  tampered_details still carries the per-row evidence.
        if tampered:
            logger.warning(
                "integrity_verification_summary",
                total=total,
                verified=verified,
                tampered=len(tampered),
                no_hash=len(missing_hash_keys),
                likely_key_mismatch=likely_key_mismatch,
                sample_tampered_keys=tampered[:5],
                hint=(
                    "all entries failed — likely signing-key mismatch, not tampering; "
                    "run `tapps-brain maintenance resign-integrity` if the data is trusted"
                    if likely_key_mismatch
                    else "some entries failed integrity verification"
                ),
            )

        return {
            "total": total,
            "verified": verified,
            "tampered": len(tampered),
            "no_hash": len(missing_hash_keys),
            "tampered_keys": tampered,
            "missing_hash_keys": missing_hash_keys,
            "tampered_details": tampered_details,
            "likely_key_mismatch": likely_key_mismatch,
        }

    def rehash_integrity_v1(self) -> dict[str, int]:
        """Recompute integrity hashes for legacy v1 (pipe-joined) entries.

        Scans all in-memory entries whose ``integrity_hash_v == 1`` (written
        before TAP-710 was fixed), verifies each against the old v1 canonical
        form, and — if the stored hash is still valid — replaces it with a
        fresh v2 (JSON) hash.  Entries whose v1 hash no longer matches (i.e.
        already tampered) are left unchanged and counted in ``tampered``.
        Entries with no hash are skipped and counted in ``skipped_no_hash``.

        This method is the application-layer migration shim for upgrading from
        ``integrity_hash_v = 1`` to ``integrity_hash_v = 2``.  After running
        it, :meth:`verify_integrity` will validate all entries under the v2
        scheme.  The shim is safe to run multiple times — v2 entries are a
        no-op.

        Returns:
            Dict with ``upgraded``, ``tampered``, ``skipped_no_hash``,
            ``already_v2`` counts.
        """
        upgraded = 0
        tampered = 0
        skipped_no_hash = 0
        already_v2 = 0

        with self._serialized():
            keys = list(self._entries.keys())

        for key in keys:
            with self._serialized():
                entry = self._entries.get(key)
            if entry is None:
                continue

            stored_hash = getattr(entry, "integrity_hash", None)
            if not stored_hash:
                skipped_no_hash += 1
                continue

            hash_version = getattr(entry, "integrity_hash_v", 1)
            if hash_version >= 2:
                already_v2 += 1
                continue

            tier_str, source_str = _hash_field_strs(entry)

            # Verify that the stored v1 hash is still intact before upgrading.
            v1_expected = compute_integrity_hash_v1(entry.key, entry.value, tier_str, source_str)
            if not hmac.compare_digest(v1_expected, stored_hash):
                tampered += 1
                logger.warning(
                    "rehash_integrity_v1.tampered_skipped",
                    key=key,
                    hint="v1 hash mismatch — entry may be tampered; not upgraded",
                )
                continue

            # v1 hash is intact — upgrade to v2.
            new_hash = compute_integrity_hash(entry.key, entry.value, tier_str, source_str)
            upgraded_entry = entry.model_copy(
                update={"integrity_hash": new_hash, "integrity_hash_v": INTEGRITY_HASH_VERSION}
            )
            with self._serialized():
                previous = entry
                self._entries[key] = upgraded_entry

            # TAP-2857: persist the rehash to the private backend so the v2 hash
            # survives restarts. Count only after a durable write succeeds.
            try:
                self._persistence.save(upgraded_entry)
            except Exception:
                with self._serialized():
                    if self._entries.get(key) is upgraded_entry:
                        self._entries[key] = previous
                logger.warning("rehash_integrity_v1.persist_failed", key=key, exc_info=True)
                continue

            if self._hive_store is not None:
                self._propagate_to_hive(upgraded_entry)

            upgraded += 1
            logger.debug("rehash_integrity_v1.upgraded", key=key)

        logger.info(
            "rehash_integrity_v1.complete",
            upgraded=upgraded,
            tampered=tampered,
            skipped_no_hash=skipped_no_hash,
            already_v2=already_v2,
        )
        return {
            "upgraded": upgraded,
            "tampered": tampered,
            "skipped_no_hash": skipped_no_hash,
            "already_v2": already_v2,
        }

    def resign_integrity(self) -> dict[str, int]:
        """Re-sign every entry's integrity hash under the CURRENT signing key (TAP-4331).

        Operator remediation for a signing-key mismatch — e.g. a database volume
        restored under a host whose ``~/.tapps-brain/integrity.key`` (or
        ``TAPPS_BRAIN_INTEGRITY_KEY`` env) differs from the key that originally
        wrote the rows, so :meth:`verify_integrity` reports every row as tampered.

        This **assumes the stored content is authentic** and overwrites
        ``integrity_hash`` + ``integrity_hash_v`` with a fresh v2 hash, destroying
        the prior tamper-audit trail.  Only run with explicit operator intent and
        only when the data is trusted (see ``maintenance resign-integrity``).

        Returns:
            Dict with ``resigned`` and ``skipped_no_change`` counts.
        """
        self._merge_durable_entries()
        with self._serialized():
            keys = list(self._entries.keys())

        resigned = 0
        skipped_no_change = 0

        for key in keys:
            with self._serialized():
                entry = self._entries.get(key)
            if entry is None:
                continue

            tier_str, source_str = _hash_field_strs(entry)
            new_hash = compute_integrity_hash(entry.key, entry.value, tier_str, source_str)

            if (
                getattr(entry, "integrity_hash", None) == new_hash
                and getattr(entry, "integrity_hash_v", 1) == INTEGRITY_HASH_VERSION
            ):
                skipped_no_change += 1
                continue

            resigned_entry = entry.model_copy(
                update={"integrity_hash": new_hash, "integrity_hash_v": INTEGRITY_HASH_VERSION}
            )
            with self._serialized():
                previous = entry
                self._entries[key] = resigned_entry

            try:
                self._persistence.save(resigned_entry)
            except Exception:
                with self._serialized():
                    if self._entries.get(key) is resigned_entry:
                        self._entries[key] = previous
                logger.warning("resign_integrity.persist_failed", key=key, exc_info=True)
                continue

            if self._hive_store is not None:
                self._propagate_to_hive(resigned_entry)

            resigned += 1

        logger.warning(
            "resign_integrity.complete",
            resigned=resigned,
            skipped_no_change=skipped_no_change,
            hint="integrity hashes rewritten under the current signing key",
        )
        return {"resigned": resigned, "skipped_no_change": skipped_no_change}
