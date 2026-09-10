"""TAP-7329 — falsy project_id must not silently reuse default_store under strict mode.

``_get_store_for_project`` (``mcp_server/context.py``) special-cased a falsy
``project_id`` (and no per-call agent override) by returning the server's
``default_store`` unconditionally. Under ``TAPPS_BRAIN_STRICT_PROJECTS=1``
that bypasses the registry/refusal path entirely: a request whose tenant
never resolved silently reads/writes the default tenant's data instead of
being refused, exactly the failure the ``/mcp`` fix in
``http/middleware.py`` closes on the header side.

Lax mode (the default, both flags unset) must be byte-identical to base —
stdio single-tenant callers with no ``TAPPS_BRAIN_PROJECT`` set rely on the
default-store fallback.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tapps_brain.mcp_server.context import _get_store_for_project
from tapps_brain.project_registry import ProjectNotRegisteredError


class TestFalsyProjectIdFallback:
    def test_lax_mode_falls_back_to_default_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_STRICT_PROJECTS", raising=False)
        default_store = MagicMock()
        result = _get_store_for_project(None, default_store=default_store)
        assert result is default_store

    def test_empty_string_project_id_lax_mode_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_STRICT_PROJECTS", raising=False)
        default_store = MagicMock()
        result = _get_store_for_project("", default_store=default_store)
        assert result is default_store

    def test_strict_mode_refuses_instead_of_defaulting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_PROJECTS", "1")
        default_store = MagicMock()
        with pytest.raises(ProjectNotRegisteredError):
            _get_store_for_project(None, default_store=default_store)

    def test_strict_mode_empty_string_also_refuses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_PROJECTS", "1")
        default_store = MagicMock()
        with pytest.raises(ProjectNotRegisteredError):
            _get_store_for_project("", default_store=default_store)
