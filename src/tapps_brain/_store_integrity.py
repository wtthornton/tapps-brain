"""Integrity-hash verification for :class:`~tapps_brain.store.MemoryStore` (TAP-2833).

Extracted from ``store.py`` as a mixin.  Covers HMAC integrity verification
(H4b) and the v1->v2 rehash migration shim (TAP-710).

TAP-2857: the upgrade branch of :meth:`rehash_integrity_v1` previously referenced
a non-existent ``self._backend`` attribute (raising ``AttributeError`` and never
persisting the rehash); it now persists via ``self._persistence``.
"""

from __future__ import annotations

import contextlib
from typing import Any

import structlog

from tapps_brain._store_base import _MemoryStoreBase

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


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
        from tapps_brain.integrity import (
            compute_integrity_hash,
            compute_integrity_hash_v1,
            verify_integrity_hash,
        )

        self._metrics.increment("store.verify_integrity")

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

            tier_str = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
            source_str = entry.source.value if hasattr(entry.source, "value") else str(entry.source)
            hash_version = getattr(entry, "integrity_hash_v", 1)

            # Use the version-appropriate verifier so legacy v1 rows don't
            # spuriously show as tampered when the process uses the v2 scheme.
            if hash_version == 1:
                # v1: legacy pipe-joined canonical form
                v1_expected = compute_integrity_hash_v1(
                    entry.key, entry.value, tier_str, source_str
                )
                import hmac as _hmac

                if _hmac.compare_digest(v1_expected, stored_hash):
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
            logger.warning(
                "integrity_verification_failed",
                key=entry.key,
                tier=tier_str,
                hash_version=hash_version,
            )

        return {
            "total": total,
            "verified": verified,
            "tampered": len(tampered),
            "no_hash": len(missing_hash_keys),
            "tampered_keys": tampered,
            "missing_hash_keys": missing_hash_keys,
            "tampered_details": tampered_details,
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
        import hmac as _hmac

        from tapps_brain.integrity import (
            INTEGRITY_HASH_VERSION as _HASH_V,
        )
        from tapps_brain.integrity import (
            compute_integrity_hash,
            compute_integrity_hash_v1,
        )

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

            tier_str = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
            source_str = entry.source.value if hasattr(entry.source, "value") else str(entry.source)

            # Verify that the stored v1 hash is still intact before upgrading.
            v1_expected = compute_integrity_hash_v1(entry.key, entry.value, tier_str, source_str)
            if not _hmac.compare_digest(v1_expected, stored_hash):
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
                update={"integrity_hash": new_hash, "integrity_hash_v": _HASH_V}
            )
            with self._lock:
                self._entries[key] = upgraded_entry

            if self._hive_store is not None:
                with contextlib.suppress(Exception):
                    self._hive_store.save(upgraded_entry)  # type: ignore[call-arg,arg-type,misc]

            # TAP-2857: persist the rehash to the private backend so the v2 hash
            # survives restarts.  Best-effort (suppressed) — a single failed
            # write must not abort the rest of the migration.
            with contextlib.suppress(Exception):
                self._persistence.save(upgraded_entry)

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
