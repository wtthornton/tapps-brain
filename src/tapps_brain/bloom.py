"""Bloom filter for fast approximate membership testing.

Used as a write-path dedup fast-path. If the filter says "definitely not seen",
skip expensive similarity check. If "maybe seen", do full check.
Pure Python, no external dependencies.
"""
from __future__ import annotations

import hashlib
import math


class BloomFilter:
    """Simple Bloom filter with configurable size and hash count."""

    def __init__(self, expected_items: int = 5000, fp_rate: float = 0.01) -> None:
        # Calculate optimal size and hash count
        self._size = max(64, self._optimal_size(expected_items, fp_rate))
        self._hash_count = max(1, self._optimal_hashes(self._size, expected_items))
        self._bits = bytearray(self._size // 8 + 1)
        self._count = 0

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
        h1 = int(hashlib.md5(item.encode()).hexdigest(), 16)
        h2 = int(hashlib.sha1(item.encode()).hexdigest(), 16)
        return [(h1 + i * h2) % self._size for i in range(self._hash_count)]

    def add(self, item: str) -> None:
        """Add an item to the filter."""
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


def normalize_for_dedup(text: str) -> str:
    """Normalize text for dedup comparison — lowercase, collapse whitespace."""
    return " ".join(text.lower().split())
