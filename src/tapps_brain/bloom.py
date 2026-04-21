"""Bloom filter for fast approximate membership testing.

Used as a write-path dedup fast-path. If the filter says "definitely not seen",
skip expensive similarity check. If "maybe seen", do full check.
Pure Python, no external dependencies.

**Nominal false-positive rate**

For a filter sized with ``expected_items=n`` and ``fp_rate=p``, the constructor
chooses bit length *m* and hash count *k* using the usual Bloom optima
(*m* ~ - *n* ln *p* / (ln 2)^2, *k* ~ (*m*/*n*) ln 2). After *i* insertions, the
approximate probability that a **non-member** string still tests positive is
``(1 - exp(-k*i/m))^k``. That stays near *p* while *i* stays near *n*; many more
inserts than *n* raise false positives (extra reinforce-path work, not duplicate
rows from the filter alone). Use :func:`bloom_false_positive_probability` or
:meth:`BloomFilter.approximate_false_positive_rate` for numeric estimates.

**Defaults:** ``BloomFilter()`` uses ``expected_items=5000`` and ``fp_rate=0.01``
(~1% nominal FP at capacity). Tighten *p* or raise *expected_items* if the
store cap or traffic grows.
"""

from __future__ import annotations

import hashlib
import logging
import math
import unicodedata
from collections.abc import Iterable

_log = logging.getLogger(__name__)


def bloom_false_positive_probability(bit_size: int, hash_count: int, inserted_count: int) -> float:
    """Approximate Bloom false-positive rate for a random non-member after *inserted_count* adds.

    Uses the standard independence approximation
    ``(1 - exp(-k*n/m))^k`` with *m* = ``bit_size``, *k* = ``hash_count``, *n* =
    ``inserted_count``. This matches the **nominal** *fp_rate* passed to
    :class:`BloomFilter` when *inserted_count* ≈ *expected_items* and *m*, *k*
    come from the same sizing rules.
    """
    if bit_size <= 0 or inserted_count < 0:
        return 1.0
    m = float(bit_size)
    k = float(hash_count)
    n = float(inserted_count)
    return min(1.0, float((1.0 - math.exp(-k * n / m)) ** k))


class BloomFilter:
    """Bloom filter with size/hash count derived from expected load and target FP rate.

    See module docstring for the false-positive model and default (~1% at 5k inserts).
    """

    def __init__(self, expected_items: int = 5000, fp_rate: float = 0.01) -> None:
        self._expected_items = max(1, expected_items)
        self._fp_rate = fp_rate
        self._size = max(64, self._optimal_size(expected_items, fp_rate))
        self._hash_count = max(1, self._optimal_hashes(self._size, expected_items))
        self._bits = bytearray(self._size // 8 + 1)
        self._count = 0

    @property
    def bit_size(self) -> int:
        """Length of the bit array used for hashing (not necessarily a multiple of 8)."""
        return self._size

    @property
    def hash_count(self) -> int:
        """Number of hash functions *k*."""
        return self._hash_count

    @staticmethod
    def _optimal_size(n: int, p: float) -> int:
        """Optimal bit array size: m = -(n * ln(p)) / (ln(2)^2)"""
        if n <= 0 or p <= 0 or p >= 1:
            return 512
        m = -(n * math.log(p)) / (math.log(2) ** 2)
        return int(m) + 1

    @staticmethod
    def _optimal_hashes(m: int, n: int) -> int:
        """Optimal hash count: k = (m/n) * ln(2)"""
        if n <= 0:
            return 7
        k = (m / n) * math.log(2)
        return max(1, int(k))

    def _get_hashes(self, item: str) -> list[int]:
        """Generate k hash positions using double hashing."""
        h1 = int(hashlib.md5(item.encode(), usedforsecurity=False).hexdigest(), 16)
        h2 = int(hashlib.sha1(item.encode(), usedforsecurity=False).hexdigest(), 16)
        return [(h1 + i * h2) % self._size for i in range(self._hash_count)]

    def clear(self) -> None:
        """Reset the filter to an empty state (all bits zero, count zero)."""
        self._bits = bytearray(self._size // 8 + 1)
        self._count = 0

    def rebuild(self, items: Iterable[str]) -> None:
        """Clear the filter and re-add *items*.

        Call this after GC / bulk-delete to remove stale bits from evicted
        entries and restore accurate membership information.  O(k*n) where *k*
        is ``hash_count`` and *n* is ``len(items)``.
        """
        self.clear()
        for it in items:
            self.add(it)

    def add(self, item: str) -> None:
        """Add an item to the filter.

        When ``count`` reaches or exceeds ``expected_items + expected_items // 2``
        (≥ 1.5× for even values) the filter auto-resizes
        (doubles the underlying bit array) and rebuilds from the items already
        in the filter's logical set.  This keeps the false-positive rate bounded
        rather than letting it grow without limit as the store expands beyond
        the initial capacity estimate.  Callers that previously captured items
        for a manual rebuild can skip that after the auto-resize path fires
        (the filter's bits are already updated).

        .. note::
            Auto-resize cannot enumerate the items that were previously added
            because a plain Bloom filter does not retain them.  The resize
            therefore only works when the caller is tracking items externally
            and can supply them to :meth:`rebuild` — or when the store calls
            :meth:`rebuild` right after (which :class:`MemoryStore` does on GC
            and rollback).  If auto-resize fires mid-stream (between a GC and
            the subsequent rebuild) the bits are cleared and only items added
            *after* the resize are present, which is conservative (no false
            negatives can be introduced — items not yet re-added will just
            trigger a full similarity check rather than being short-circuited).
        """
        # Auto-resize: when count reaches or exceeds expected_items + expected_items//2
        # (≥ 1.5× the design capacity for even values), double the filter's capacity
        # and clear it.  The store's GC / save paths call rebuild() after mutations,
        # so the cleared filter will be repopulated shortly.
        if self._count >= self._expected_items + (self._expected_items // 2):
            new_expected = self._expected_items * 2
            _log.warning(
                "bloom_filter_auto_resize",
                extra={
                    "old_expected": self._expected_items,
                    "new_expected": new_expected,
                    "count": self._count,
                    "fp_rate_before": self.approximate_false_positive_rate(),
                },
            )
            self._expected_items = new_expected
            self._size = max(64, self._optimal_size(new_expected, self._fp_rate))
            self._hash_count = max(1, self._optimal_hashes(self._size, new_expected))
            self._bits = bytearray(self._size // 8 + 1)
            self._count = 0

        for pos in self._get_hashes(item):
            byte_idx = pos // 8
            bit_idx = pos % 8
            self._bits[byte_idx] |= 1 << bit_idx
        self._count += 1

    def might_contain(self, item: str) -> bool:
        """Check if item might be in the filter. False = definitely not. True = maybe."""
        for pos in self._get_hashes(item):
            byte_idx = pos // 8
            bit_idx = pos % 8
            if not (self._bits[byte_idx] & (1 << bit_idx)):
                return False
        return True

    @property
    def count(self) -> int:
        return self._count

    def approximate_false_positive_rate(self) -> float:
        """FP approximation for a non-member after ``count`` inserts (see module doc)."""
        return bloom_false_positive_probability(self._size, self._hash_count, self._count)


def normalize_for_dedup(text: str) -> str:
    """Normalize text for dedup comparison — Unicode NFKC, lowercase, collapse whitespace.

    NFKC folds compatibility characters (e.g. fullwidth Latin) so visually
    similar strings match the same dedup key as the save-path Bloom filter and
    full string equality check (EPIC-044 STORY-044.2).
    """
    if not text:
        return ""
    nfkc = unicodedata.normalize("NFKC", text)
    return " ".join(nfkc.lower().split())
