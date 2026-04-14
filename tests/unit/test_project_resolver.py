"""Unit tests for :mod:`tapps_brain.project_resolver` (EPIC-069 STORY-069.3)."""

from __future__ import annotations

import pytest

from tapps_brain.project_resolver import (
    DEFAULT_PROJECT_ID,
    ENV_VAR,
    HEADER_NAME,
    InvalidProjectIdError,
    resolve_project_id,
    validate_project_id,
)


class TestValidateProjectId:
    @pytest.mark.parametrize(
        "pid",
        ["alpaca", "tapps-brain-dev", "p1", "a_b_c", "a" * 64, "0a", "default"],
    )
    def test_valid_slugs(self, pid: str) -> None:
        assert validate_project_id(pid) == pid

    @pytest.mark.parametrize(
        "pid",
        ["", "Alpaca", "UPPER", "-leading-dash", "_leading", "has space", "a" * 65, "bad!char"],
    )
    def test_invalid_slugs(self, pid: str) -> None:
        with pytest.raises(InvalidProjectIdError):
            validate_project_id(pid)


class TestResolvePrecedence:
    def test_meta_override_wins(self) -> None:
        result = resolve_project_id(
            meta_override="alpaca",
            headers={HEADER_NAME: "from-header"},
            env={ENV_VAR: "from-env"},
        )
        assert result == "alpaca"

    def test_header_beats_env(self) -> None:
        result = resolve_project_id(
            headers={HEADER_NAME: "from-header"},
            env={ENV_VAR: "from-env"},
        )
        assert result == "from-header"

    def test_env_when_no_header(self) -> None:
        result = resolve_project_id(headers={}, env={ENV_VAR: "from-env"})
        assert result == "from-env"

    def test_default_fallback(self) -> None:
        result = resolve_project_id(headers={}, env={})
        assert result == DEFAULT_PROJECT_ID

    def test_header_case_insensitive(self) -> None:
        result = resolve_project_id(
            headers={"x-tapps-project": "lower-header"},
            env={},
        )
        assert result == "lower-header"

    def test_blank_values_skipped(self) -> None:
        result = resolve_project_id(
            meta_override="   ",
            headers={HEADER_NAME: ""},
            env={ENV_VAR: "real-one"},
        )
        assert result == "real-one"

    def test_invalid_resolved_value_raises(self) -> None:
        with pytest.raises(InvalidProjectIdError):
            resolve_project_id(meta_override="NOT_A_SLUG")

    def test_default_passes_through_without_validation(self) -> None:
        # 'default' is the documented sentinel and is allowed even though
        # it also happens to match the slug shape.
        assert resolve_project_id(headers={}, env={}) == DEFAULT_PROJECT_ID
