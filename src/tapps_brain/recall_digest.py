"""Content-addressed handle for a recalled memory set (TAP-6583).

A recall returns memories but nothing identifying *which set* came back, so
nothing downstream can assert "this invocation used exactly this recall set".
This module supplies that handle: a stable ``(key, version)`` list plus a
SHA-256 digest over it.

**Plain SHA-256, deliberately not the HMAC in** :mod:`tapps_brain.integrity`.
That HMAC is keyed from a per-installation secret and answers "was this row
tampered with"; it is not reproducible on another machine, so it cannot serve
as a content address. The two answer different questions and are kept apart.

**Version = content, not timestamp.** ``MemoryEntry`` has no monotonic version
column, and the value that reaches the prompt may differ from the stored value
(RAG safety sanitises in place). Hashing the injected value is therefore the
only version that is both available at the digest site and guaranteed to change
whenever the recalled content changes.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from tapps_brain.models import MemoryVersion

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = ["VERSION_HEX_LENGTH", "compute_recall_digest", "memory_version"]

# Truncated to keep ``memory_versions`` compact on the wire. 64 bits of
# content hash is far past collision risk for a set of at most a few dozen
# memories, and the full-width digest above it is what callers actually pin.
VERSION_HEX_LENGTH = 16


def memory_version(value: str) -> str:
    """Return the content version of one injected memory value."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:VERSION_HEX_LENGTH]


def compute_recall_digest(
    memories: Iterable[Mapping[str, Any]],
) -> tuple[str, list[MemoryVersion]]:
    """Return ``(recall_digest, memory_versions)`` for an injected memory set.

    Call this on the set that actually reached the prompt — after every
    truncation, post-filter, and Hive merge. A digest over the candidate pool
    would not describe the prompt, which defeats the point.

    The digest hashes the ``(key, version)`` pairs **sorted**, so two recalls
    that return the same memories in a different row order agree. The returned
    ``memory_versions`` list keeps the injected order, which carries the ranking
    the digest deliberately discards.

    An empty set yields ``("", [])`` — the same values as the field defaults, so
    a caller cannot tell an empty recall from a pre-digest one and does not have
    to.
    """
    versions = [
        MemoryVersion(
            key=str(mem.get("key", "")),
            version=memory_version(str(mem.get("value", "") or "")),
        )
        for mem in memories
    ]
    if not versions:
        return "", []
    canonical = json.dumps(
        sorted((v.key, v.version) for v in versions),
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), versions
