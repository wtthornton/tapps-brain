"""Unit tests for profile tier migration helpers (GitHub #20)."""

from __future__ import annotations

import pytest

from tapps_brain.profile import get_builtin_profile
from tapps_brain.profile_migrate import (
    coerce_tier_value,
    parse_tier_map_json,
    parse_tier_map_pairs,
    validate_tier_map,
)


class TestParseTierMapPairs:
    def test_parses_and_last_wins(self) -> None:
        m = parse_tier_map_pairs(["a:b", " c:d ", "a:x"])
        assert m == {"a": "x", "c": "d"}

    def test_skips_blank_lines(self) -> None:
        m = parse_tier_map_pairs(["a:b", "   ", ""])
        assert m == {"a": "b"}

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="from:to"):
            parse_tier_map_pairs(["nocolon"])

    def test_empty_part_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            parse_tier_map_pairs([":b"])


class TestParseTierMapJson:
    def test_object(self) -> None:
        m = parse_tier_map_json('{"old":"pattern"}')
        assert m == {"old": "pattern"}

    def test_non_string_value_raises(self) -> None:
        with pytest.raises(ValueError, match="strings"):
            parse_tier_map_json('{"a": "pattern", "b": 3}')

    def test_empty_value_raises(self) -> None:
        with pytest.raises(ValueError, match="empty tier"):
            parse_tier_map_json('{"a": "  "}')

    def test_not_object_raises(self) -> None:
        with pytest.raises(ValueError, match="JSON object"):
            parse_tier_map_json("[]")


class TestValidateTierMap:
    def test_empty_errors(self) -> None:
        assert validate_tier_map({}, None) == ["tier map is empty"]

    def test_whitespace_only_mapping(self) -> None:
        err = validate_tier_map({"  ": "pattern"}, None)
        assert len(err) == 1
        assert "empty tier" in err[0]

    def test_bad_target(self) -> None:
        err = validate_tier_map({"anything": "not_a_real_tier_ever"}, None)
        assert len(err) == 1
        assert "not_a_real_tier_ever" in err[0]

    def test_valid_builtin_target(self) -> None:
        assert validate_tier_map({"legacy": "pattern"}, None) == []


class TestCoerceTierValue:
    def test_enum(self) -> None:
        assert coerce_tier_value("pattern", None).value == "pattern"

    def test_unknown_without_profile(self) -> None:
        with pytest.raises(ValueError, match="Unknown"):
            coerce_tier_value("custom_layer_xyz", None)

    def test_profile_layer(self) -> None:
        profile = get_builtin_profile("repo-brain")
        first = profile.layers[0].name
        v = coerce_tier_value(first, profile)
        assert v == first

    def test_custom_layer_name_not_memory_tier(self) -> None:
        profile = get_builtin_profile("personal-assistant")
        v = coerce_tier_value("identity", profile)
        assert v == "identity"
