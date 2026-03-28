"""Tests for BloomFilter and normalize_for_dedup."""

from __future__ import annotations

from tapps_brain.bloom import BloomFilter, normalize_for_dedup

# ---------------------------------------------------------------------------
# normalize_for_dedup
# ---------------------------------------------------------------------------


class TestNormalizeForDedup:
    def test_lowercases(self) -> None:
        assert normalize_for_dedup("Hello World") == "hello world"

    def test_collapses_whitespace(self) -> None:
        assert normalize_for_dedup("  foo   bar  ") == "foo bar"

    def test_tabs_and_newlines(self) -> None:
        assert normalize_for_dedup("foo\t\nbar") == "foo bar"

    def test_empty_string(self) -> None:
        assert normalize_for_dedup("") == ""

    def test_already_normalized(self) -> None:
        assert normalize_for_dedup("already normal") == "already normal"

    def test_mixed_case_and_spaces(self) -> None:
        assert normalize_for_dedup("  THE  Quick  Brown  FOX  ") == "the quick brown fox"


# ---------------------------------------------------------------------------
# BloomFilter
# ---------------------------------------------------------------------------


class TestBloomFilter:
    def test_default_construction(self) -> None:
        bf = BloomFilter()
        assert bf.count == 0

    def test_add_increments_count(self) -> None:
        bf = BloomFilter()
        bf.add("hello")
        assert bf.count == 1
        bf.add("world")
        assert bf.count == 2

    def test_might_contain_after_add(self) -> None:
        bf = BloomFilter()
        bf.add("test item")
        assert bf.might_contain("test item") is True

    def test_might_contain_false_for_unseen(self) -> None:
        bf = BloomFilter(expected_items=1000, fp_rate=0.001)
        bf.add("foo")
        # A completely different string should not be reported as contained
        # (very low fp_rate makes this near-certain)
        assert bf.might_contain("zzz_definitely_not_there_xyz_123") is False

    def test_no_false_negatives(self) -> None:
        """Items that were added must always be reported as might_contain."""
        bf = BloomFilter(expected_items=200, fp_rate=0.01)
        items = [f"memory-entry-{i}" for i in range(100)]
        for item in items:
            bf.add(item)
        for item in items:
            assert bf.might_contain(item) is True, f"False negative for: {item!r}"

    def test_count_reflects_all_adds(self) -> None:
        bf = BloomFilter()
        for i in range(50):
            bf.add(f"item-{i}")
        assert bf.count == 50

    def test_custom_fp_rate(self) -> None:
        bf = BloomFilter(expected_items=100, fp_rate=0.001)
        bf.add("precise item")
        assert bf.might_contain("precise item") is True

    def test_small_expected_items(self) -> None:
        """Edge case: very small expected_items shouldn't crash."""
        bf = BloomFilter(expected_items=1, fp_rate=0.01)
        bf.add("x")
        assert bf.might_contain("x") is True

    def test_zero_expected_items_fallback(self) -> None:
        """n=0 triggers the fallback size (512)."""
        bf = BloomFilter(expected_items=0, fp_rate=0.01)
        bf.add("y")
        assert bf.might_contain("y") is True

    def test_optimal_size_extreme_fp(self) -> None:
        """fp_rate >= 1 triggers fallback."""
        size = BloomFilter._optimal_size(100, 1.0)
        assert size == 512

    def test_optimal_size_zero_fp(self) -> None:
        """fp_rate = 0 triggers fallback."""
        size = BloomFilter._optimal_size(100, 0.0)
        assert size == 512

    def test_optimal_hashes_zero_n(self) -> None:
        """n=0 triggers fallback hash count of 7."""
        k = BloomFilter._optimal_hashes(1000, 0)
        assert k == 7

    def test_separate_filters_independent(self) -> None:
        bf1 = BloomFilter()
        bf2 = BloomFilter()
        bf1.add("shared")
        assert bf1.might_contain("shared") is True
        # bf2 is independent — "shared" was not added to it
        assert bf2.might_contain("shared") is False

    def test_unicode_item(self) -> None:
        bf = BloomFilter()
        bf.add("こんにちは世界")
        assert bf.might_contain("こんにちは世界") is True
        assert bf.might_contain("hello world") is False

    def test_empty_string_item(self) -> None:
        bf = BloomFilter()
        bf.add("")
        assert bf.might_contain("") is True
