"""Tests for unicode, emoji, CJK, RTL, and boundary value edge cases.

Story-016.6: Verify that the store correctly handles non-ASCII content in
values and enforces key/value length limits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tapps_brain.models import MAX_KEY_LENGTH, MAX_VALUE_LENGTH
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture()
def store(tmp_path: Path) -> Generator[MemoryStore, None, None]:
    """Create a MemoryStore backed by a temp directory."""
    s = MemoryStore(tmp_path)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Emoji in values
# ---------------------------------------------------------------------------


class TestEmojiInValues:
    """Emoji characters are valid in values (not keys)."""

    def test_save_and_get_emoji_value(self, store: MemoryStore) -> None:
        """Saving a value with emoji round-trips correctly."""
        store.save(key="emoji-test", value="Hello 🎉 World 🌍!")
        loaded = store.get("emoji-test")
        assert loaded is not None
        assert "🎉" in loaded.value
        assert "🌍" in loaded.value

    def test_recall_with_emoji_value(self, store: MemoryStore) -> None:
        """Recall returns entries whose values contain emoji."""
        store.save(key="emoji-recall", value="Rocket launch 🚀 successful")
        results = store.recall("Rocket launch")
        assert any(e.key == "emoji-recall" for e in results.memories)

    def test_fts_search_with_emoji_value(self, store: MemoryStore) -> None:
        """FTS search finds entries with emoji in values (searches ASCII tokens)."""
        store.save(key="emoji-fts", value="celebration 🎊 party time")
        # Search by the ASCII word; the emoji is stored alongside
        results = store.search("celebration")
        assert any(e.key == "emoji-fts" for e in results)

    def test_multiple_emoji_in_value(self, store: MemoryStore) -> None:
        """Values with multiple emoji are stored and retrieved intact."""
        value = "Stars: ⭐🌟💫✨ — Fire: 🔥 — Water: 💧"
        store.save(key="multi-emoji", value=value)
        loaded = store.get("multi-emoji")
        assert loaded is not None
        assert loaded.value == value


# ---------------------------------------------------------------------------
# CJK characters in values
# ---------------------------------------------------------------------------


class TestCJKInValues:
    """CJK (Chinese, Japanese, Korean) characters are valid in values."""

    def test_save_and_get_chinese(self, store: MemoryStore) -> None:
        """Chinese characters round-trip correctly."""
        store.save(key="cjk-chinese", value="你好世界 — Hello World in Chinese")
        loaded = store.get("cjk-chinese")
        assert loaded is not None
        assert "你好世界" in loaded.value

    def test_save_and_get_japanese(self, store: MemoryStore) -> None:
        """Japanese characters (hiragana, katakana, kanji) round-trip correctly."""
        store.save(key="cjk-japanese", value="こんにちは世界 — ハロー・ワールド")
        loaded = store.get("cjk-japanese")
        assert loaded is not None
        assert "こんにちは" in loaded.value

    def test_save_and_get_korean(self, store: MemoryStore) -> None:
        """Korean (Hangul) characters round-trip correctly."""
        store.save(key="cjk-korean", value="안녕하세요 세계 — Hello World in Korean")
        loaded = store.get("cjk-korean")
        assert loaded is not None
        assert "안녕하세요" in loaded.value

    def test_recall_cjk_value(self, store: MemoryStore) -> None:
        """Recall returns entries with CJK content when queried by ASCII words."""
        store.save(key="cjk-recall", value="project status 项目状态 complete")
        results = store.recall("project status")
        assert any(e.key == "cjk-recall" for e in results.memories)


# ---------------------------------------------------------------------------
# Mixed RTL/LTR text in values
# ---------------------------------------------------------------------------


class TestRTLInValues:
    """Right-to-left (Arabic, Hebrew) text mixed with LTR text in values."""

    def test_save_and_get_arabic(self, store: MemoryStore) -> None:
        """Arabic characters round-trip correctly."""
        store.save(key="rtl-arabic", value="مرحبا بالعالم — Hello World in Arabic")
        loaded = store.get("rtl-arabic")
        assert loaded is not None
        assert "مرحبا" in loaded.value

    def test_save_and_get_hebrew(self, store: MemoryStore) -> None:
        """Hebrew characters round-trip correctly."""
        store.save(key="rtl-hebrew", value="שלום עולם — Hello World in Hebrew")
        loaded = store.get("rtl-hebrew")
        assert loaded is not None
        assert "שלום" in loaded.value

    def test_mixed_rtl_ltr(self, store: MemoryStore) -> None:
        """Mixed RTL and LTR text in a single value is stored correctly."""
        value = "English: Hello — عربي: مرحبا — 中文: 你好"
        store.save(key="rtl-mixed", value=value)
        loaded = store.get("rtl-mixed")
        assert loaded is not None
        assert loaded.value == value


# ---------------------------------------------------------------------------
# Key length boundary values
# ---------------------------------------------------------------------------


class TestKeyLengthBoundaries:
    """Key validation enforces MAX_KEY_LENGTH (128 chars)."""

    def test_key_at_max_length_accepted(self, store: MemoryStore) -> None:
        """A key exactly MAX_KEY_LENGTH characters long is accepted."""
        # First char is alphanumeric; remaining chars fill to MAX_KEY_LENGTH
        key = "a" + "b" * (MAX_KEY_LENGTH - 1)
        assert len(key) == MAX_KEY_LENGTH
        entry = store.save(key=key, value="boundary value")
        assert entry.key == key

    def test_key_one_over_max_length_rejected(self, store: MemoryStore) -> None:
        """A key one character beyond MAX_KEY_LENGTH is rejected."""
        key = "a" + "b" * MAX_KEY_LENGTH  # MAX_KEY_LENGTH + 1 total
        assert len(key) == MAX_KEY_LENGTH + 1
        with pytest.raises(Exception):  # ValidationError (Pydantic) or ValueError
            store.save(key=key, value="should fail")

    def test_key_minimum_length_accepted(self, store: MemoryStore) -> None:
        """A single-character key is accepted."""
        entry = store.save(key="a", value="single char key")
        assert entry.key == "a"

    def test_key_with_allowed_chars_at_boundary(self, store: MemoryStore) -> None:
        """Keys using dots, hyphens, and underscores at max length are accepted."""
        # Pattern: a(b-c_d.)*  — alternating allowed special chars
        key = "a" + "b-c" * 42 + "b"  # = 1 + 126 + 1 = 128 chars
        assert len(key) == MAX_KEY_LENGTH
        entry = store.save(key=key, value="special chars at boundary")
        assert entry.key == key


# ---------------------------------------------------------------------------
# Value length boundary values
# ---------------------------------------------------------------------------


class TestValueLengthBoundaries:
    """Value validation enforces MAX_VALUE_LENGTH (4096 chars)."""

    def test_value_at_max_length_accepted(self, store: MemoryStore) -> None:
        """A value exactly MAX_VALUE_LENGTH characters long is accepted."""
        value = "x" * MAX_VALUE_LENGTH
        assert len(value) == MAX_VALUE_LENGTH
        entry = store.save(key="max-value-len", value=value)
        loaded = store.get("max-value-len")
        assert loaded is not None
        assert len(loaded.value) == MAX_VALUE_LENGTH

    def test_value_one_over_max_length_rejected(self, store: MemoryStore) -> None:
        """A value one character beyond MAX_VALUE_LENGTH is rejected."""
        value = "x" * (MAX_VALUE_LENGTH + 1)
        assert len(value) == MAX_VALUE_LENGTH + 1
        with pytest.raises(Exception):  # ValidationError (Pydantic) or ValueError
            store.save(key="over-max-value", value=value)

    def test_value_with_unicode_at_max_length(self, store: MemoryStore) -> None:
        """A value with multi-byte unicode characters at max length is accepted."""
        # Each '你' is one Python character, so MAX_VALUE_LENGTH chars total
        value = "你" * MAX_VALUE_LENGTH
        assert len(value) == MAX_VALUE_LENGTH
        entry = store.save(key="unicode-max-value", value=value)
        loaded = store.get("unicode-max-value")
        assert loaded is not None
        assert len(loaded.value) == MAX_VALUE_LENGTH

    def test_value_one_char_accepted(self, store: MemoryStore) -> None:
        """A single-character value is accepted."""
        entry = store.save(key="min-value", value="x")
        assert entry.key == "min-value"
