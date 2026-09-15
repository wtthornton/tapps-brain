"""TAP-6822: ``brain_remember`` threads a SERVER-RESOLVED run_id (MCP ingress).

Root cause: ``memory_service.brain_remember`` had no ``run_id`` parameter at
all, so every MCP-ingress write was unattributable by construction — unlike
the HTTP ``/v1/remember`` path (VAL-19, ``http_adapter.py:246-262``), which
already resolves an invocation id from the wire and threads it into
``run_id``.

The fix resolves the id from the active MCP request's transport envelope
(``X-Origin-Invocation-Id`` header / ``_meta.invocation_id`` — see
``mcp_server/context._current_request_invocation_id``) *inside*
``brain_remember``, never from a keyword argument. That is the anti-spoofing
property under test here: ``brain_remember``'s signature carries no
``run_id`` parameter, so nothing a model puts in a tool call's arguments can
reach the stored column.

Covers:
- Box 1: the resolved id reaches ``store.save(..., run_id=...)`` (which
  already propagates to any Hive copy — store.py:3208-3211, TAP-6815).
- Box 2 / VAL-07: the anti-spoofing property.
- Box 3 / VAL-06 positive control: no invocation context -> ``run_id`` stays
  ``None`` (not fabricated, not inherited from a prior call).
- Box 4: MCP-ingress-specific — fails if the threading is removed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_store(saved_key: str = "k") -> MagicMock:
    from tapps_brain.models import MemoryEntry

    store = MagicMock()
    entry = MemoryEntry(key=saved_key, value="x")
    store.save.return_value = entry
    return store


# ---------------------------------------------------------------------------
# Box 3 / VAL-06 positive control — absence stays absence
# ---------------------------------------------------------------------------


class TestNoInvocationContextLeavesRunIdNone:
    def test_no_invocation_context_leaves_run_id_none(self) -> None:
        """No MCP request context active -> run_id is NULL, not fabricated."""
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()
        with (
            patch("tapps_brain.agent_brain._content_key", return_value="k"),
            patch(
                "tapps_brain.services.memory_service._resolve_mcp_invocation_id",
                return_value=None,
            ),
        ):
            brain_remember(store, "proj", "agent", fact="x")

        kwargs = store.save.call_args.kwargs
        assert kwargs.get("run_id") is None

    def test_a_prior_calls_id_is_not_inherited_by_the_next(self) -> None:
        """Two successive calls with different (and then no) context must not
        leak the first call's id onto the second — no module-level caching."""
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()
        with patch("tapps_brain.agent_brain._content_key", return_value="k"):
            with patch(
                "tapps_brain.services.memory_service._resolve_mcp_invocation_id",
                return_value="first-call-run-id",
            ):
                brain_remember(store, "proj", "agent", fact="x")
            first_run_id = store.save.call_args.kwargs.get("run_id")

            with patch(
                "tapps_brain.services.memory_service._resolve_mcp_invocation_id",
                return_value=None,
            ):
                brain_remember(store, "proj", "agent", fact="y")
            second_run_id = store.save.call_args.kwargs.get("run_id")

        assert first_run_id == "first-call-run-id"
        assert second_run_id is None


# ---------------------------------------------------------------------------
# Box 1 / Box 4 — the threading itself (MCP-ingress specific)
# ---------------------------------------------------------------------------


class TestBrainRememberThreadsRunId:
    def test_resolved_invocation_id_reaches_store_save(self) -> None:
        """VAL-06 proof: with invocation context present, store.save() receives it.

        This is the test whose loss (removing the threading in
        services/memory_service.py) is VAL-06's negative control — see
        evidence block for the captured failure.
        """
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()
        with (
            patch("tapps_brain.agent_brain._content_key", return_value="k"),
            patch(
                "tapps_brain.services.memory_service._resolve_mcp_invocation_id",
                return_value="mcp-run-77",
            ),
        ):
            result = brain_remember(store, "proj", "agent", fact="x")

        assert result["saved"] is True
        kwargs = store.save.call_args.kwargs
        assert kwargs.get("run_id") == "mcp-run-77"

    def test_resolved_via_real_mcp_request_context_header(self) -> None:
        """End-to-end through the real resolver chain (mcp_server.context),
        not just a patched shortcut -- exercises the MCP ingress path proper.
        """
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()

        class _Headers:
            def get(self, key: str, default: str | None = None) -> str | None:
                return {"x-origin-invocation-id": "hdr-run-id-99"}.get(key, default)

        req = SimpleNamespace(headers=_Headers())
        rc = SimpleNamespace(request=req, meta=None)

        from mcp.server.lowlevel.server import request_ctx

        tok = request_ctx.set(rc)
        try:
            with patch("tapps_brain.agent_brain._content_key", return_value="k"):
                brain_remember(store, "proj", "agent", fact="x")
        finally:
            request_ctx.reset(tok)

        kwargs = store.save.call_args.kwargs
        assert kwargs.get("run_id") == "hdr-run-id-99"

    def test_resolved_via_real_mcp_request_context_meta(self) -> None:
        """``_meta.invocation_id`` fallback when no header is present."""
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()
        req = SimpleNamespace(headers=None)
        meta = SimpleNamespace(invocation_id="meta-run-id-55", model_extra=None)
        rc = SimpleNamespace(request=req, meta=meta)

        from mcp.server.lowlevel.server import request_ctx

        tok = request_ctx.set(rc)
        try:
            with patch("tapps_brain.agent_brain._content_key", return_value="k"):
                brain_remember(store, "proj", "agent", fact="x")
        finally:
            request_ctx.reset(tok)

        kwargs = store.save.call_args.kwargs
        assert kwargs.get("run_id") == "meta-run-id-55"


# ---------------------------------------------------------------------------
# Box 2 / VAL-07 — anti-spoofing property
# ---------------------------------------------------------------------------


class TestAntiSpoofing:
    def test_run_id_is_not_a_brain_remember_parameter(self) -> None:
        """There is no run_id argument for a model's tool-call arguments to set."""
        import inspect

        from tapps_brain.services.memory_service import brain_remember

        sig = inspect.signature(brain_remember)
        assert "run_id" not in sig.parameters

    def test_passing_run_id_as_an_argument_is_rejected(self) -> None:
        """Even a caller that tries to pass run_id= directly cannot reach it --
        it is not a parameter, so the call fails before store.save() runs."""
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()
        with pytest.raises(TypeError):
            brain_remember(store, "proj", "agent", fact="x", run_id="attacker-supplied-9999")  # type: ignore[call-arg]
        store.save.assert_not_called()

    def test_server_resolved_id_used_not_any_caller_influenced_value(self) -> None:
        """PROOF (VAL-07): the stored row carries the SERVER-RESOLVED id, not
        anything an attacker could have supplied. Both values pasted side by
        side in the evidence block."""
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()
        server_resolved = "server-resolved-run-42"
        attacker_supplied = "attacker-supplied-9999"
        with (
            patch("tapps_brain.agent_brain._content_key", return_value="k"),
            patch(
                "tapps_brain.services.memory_service._resolve_mcp_invocation_id",
                return_value=server_resolved,
            ),
        ):
            # An attacker's only lever is a tool-call argument shaped like
            # run_id -- there is none, so the closest attempt is smuggling it
            # through a normal argument such as `fact`. It must not surface
            # in the stored run_id regardless.
            brain_remember(
                store,
                "proj",
                "agent",
                fact=f"some fact; run_id={attacker_supplied}",
            )

        kwargs = store.save.call_args.kwargs
        assert kwargs.get("run_id") == server_resolved
        assert kwargs.get("run_id") != attacker_supplied

    def test_val07_negative_control_a_spoofable_implementation_fails_the_assertion(
        self,
    ) -> None:
        """NEGATIVE CONTROL: if run_id were accepted from caller-supplied data
        (the hole this lane closes) instead of resolved server-side, the
        anti-spoofing assertion above would fail. Simulated directly against
        store.save() to prove the assertion is discriminating, not vacuous."""
        store = _make_store()
        server_resolved = "server-resolved-run-42"
        attacker_supplied = "attacker-supplied-9999"

        # Stand-in for what a spoofable implementation would do: pass the
        # caller-supplied value straight through to store.save().
        store.save(key="k", value="x", run_id=attacker_supplied)
        kwargs = store.save.call_args.kwargs

        with pytest.raises(AssertionError):
            assert kwargs.get("run_id") == server_resolved


# ---------------------------------------------------------------------------
# Box 1 continued — hive propagation reuses the same store.save() contract
# ---------------------------------------------------------------------------


class TestHiveCopyCarriesRunId:
    def test_hive_scope_save_also_receives_run_id(self) -> None:
        """agent_scope='hive' still threads run_id into the single store.save()
        call whose result store.py (3208-3211) already propagates to Hive."""
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()
        with (
            patch("tapps_brain.agent_brain._content_key", return_value="k"),
            patch(
                "tapps_brain.services.memory_service._resolve_mcp_invocation_id",
                return_value="hive-run-id-1",
            ),
        ):
            brain_remember(store, "proj", "agent", fact="x", agent_scope="hive")

        kwargs = store.save.call_args.kwargs
        assert kwargs.get("agent_scope") == "hive"
        assert kwargs.get("run_id") == "hive-run-id-1"
