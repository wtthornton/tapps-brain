"""Agent Brain MCP tool registrations (EPIC-057).

Extracted from ``tapps_brain.mcp_server.__init__`` (TAP-605).  Each
``@mcp.tool()`` here is a thin wrapper that resolves the per-call store,
delegates to :mod:`tapps_brain.services.memory_service`, and serialises the
result to JSON.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tapps_brain.mcp_server.context import ToolContext

from tapps_brain.mcp_server.context import (
    _mcp_idempotency_check,
    _mcp_idempotency_record,
)
from tapps_brain.services import memory_service


def register_brain_tools(mcp: Any, ctx: ToolContext) -> None:  # noqa: ANN401, PLR0915
    """Register the six Agent Brain tools on *mcp*."""
    _server_aid = ctx.server_agent_id
    _resolve = ctx.resolve_store_for_call
    _pid = ctx.pid
    _rpc = ctx.resolve_per_call_agent_id

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_remember(
        fact: str,
        tier: str = "procedural",
        share: bool = False,
        share_with: str = "",
        agent_scope: str = "",
        memory_group: str = "",
        agent_id: str = "",
        temporal_sensitivity: str | None = None,
        failed_approaches: list[str] | None = None,
        skip_consolidation: bool = False,
    ) -> str:
        """Save a memory to the agent's brain.

        Use tier='architectural' for lasting decisions, 'pattern' for conventions,
        'procedural' for how-to knowledge.

        Scope (TAP-989): pass ``agent_scope`` directly as one of
        ``"private"`` / ``"domain"`` / ``"hive"`` / ``"group:<name>"`` for
        explicit Hive-namespace control. When ``agent_scope`` is empty, the
        legacy ``share`` / ``share_with`` params are derived for back-compat:
        ``share=True`` → ``"group"``; ``share_with="hive"`` → ``"hive"``;
        ``share_with="<x>"`` → ``"group:<x>"``. Explicit ``agent_scope`` wins.

        ``memory_group`` is a project-local partition (orthogonal to the Hive
        scope axis) — leave empty unless you need group-filtered retrieval.

        Pass ``agent_id`` to override the server-level default for this call
        (STORY-070.7).

        Pass ``temporal_sensitivity='high'`` for facts that change quickly (decays
        4x faster), ``'low'`` for stable facts (decays 4x slower), or omit for the
        tier default.

        Pass ``failed_approaches`` to record dead-end investigation paths so future
        agents don't repeat them (max 5 items).  These are surfaced in brain_recall
        responses when non-empty.

        Pass ``skip_consolidation=True`` for a long, self-contained artifact that
        must not be folded into a neighbouring entry's merged summary.

        When ``TAPPS_BRAIN_IDEMPOTENCY=1``, pass ``_meta.idempotency_key`` (UUID)
        for duplicate-safe writes.
        """
        project_id = _pid()
        ikey, dsn, cached = _mcp_idempotency_check(project_id, "brain_remember")
        if cached is not None:
            return json.dumps(cached)
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        result = memory_service.brain_remember(
            s,
            project_id,
            eff_aid,
            fact=fact,
            tier=tier,
            share=share,
            share_with=share_with,
            agent_scope=agent_scope,
            memory_group=memory_group,
            temporal_sensitivity=temporal_sensitivity,
            failed_approaches=failed_approaches,
            skip_consolidation=skip_consolidation,
        )
        if ikey and dsn:
            _mcp_idempotency_record(dsn, project_id, ikey, result, "brain_remember")
        return json.dumps(result)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_recall(
        query: str,
        max_results: int = 5,
        agent_id: str = "",
        include_sources: bool = False,
        include_contradicted: bool = False,
    ) -> str:
        """Recall memories matching a query.

        Pass ``agent_id`` to override the server-level default for this call
        (STORY-070.7).

        Pass ``include_sources=True`` to also return the original entries a
        consolidated memory was merged from — the merged value is capped and
        summarised, so this is how you get the untruncated originals back.

        Pass ``include_contradicted=True`` to also return entries that lost a
        save-time conflict against a newer write. Off by default; useful for
        auditing what was invalidated before running
        ``maintenance save-conflict-undo``.
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.brain_recall(
                s,
                _pid(),
                eff_aid,
                query=query,
                max_results=max_results,
                include_sources=include_sources,
                include_contradicted=include_contradicted,
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_forget(key: str, agent_id: str = "") -> str:
        """Archive a memory by key (to gc_archive); not permanently deleted."""
        project_id = _pid()
        ikey, dsn, cached = _mcp_idempotency_check(project_id, "brain_forget")
        if cached is not None:
            return json.dumps(cached)
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        result = memory_service.brain_forget(s, project_id, eff_aid, key=key)
        if ikey and dsn:
            _mcp_idempotency_record(dsn, project_id, ikey, result, "brain_forget")
        return json.dumps(result)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_learn_success(
        description: str = "",
        task_id: str = "",
        agent_id: str = "",
    ) -> str:
        """Record a successful task outcome.

        Required: ``description`` (free-text describing the task that
        succeeded). Aligns with ``brain_learn_failure(description=...)``.
        """
        if not description.strip():
            return json.dumps({"error": "bad_request", "detail": "'description' is required."})

        project_id = _pid()
        ikey, dsn, cached = _mcp_idempotency_check(project_id, "brain_learn_success")
        if cached is not None:
            return json.dumps(cached)
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        result = memory_service.brain_learn_success(
            s,
            project_id,
            eff_aid,
            task_description=description,
            task_id=task_id,
        )
        if ikey and dsn:
            _mcp_idempotency_record(dsn, project_id, ikey, result, "brain_learn_success")
        return json.dumps(result)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_learn_failure(
        description: str,
        task_id: str = "",
        error: str = "",
        agent_id: str = "",
    ) -> str:
        """Record a failed task outcome to avoid repeating mistakes.

        Required: ``description`` — an empty value would collapse every call
        onto the same content-hash key, silently overwriting prior failures.
        """
        if not description.strip():
            return json.dumps({"error": "bad_request", "detail": "'description' is required."})

        project_id = _pid()
        ikey, dsn, cached = _mcp_idempotency_check(project_id, "brain_learn_failure")
        if cached is not None:
            return json.dumps(cached)
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        result = memory_service.brain_learn_failure(
            s,
            project_id,
            eff_aid,
            description=description,
            task_id=task_id,
            error=error,
        )
        if ikey and dsn:
            _mcp_idempotency_record(dsn, project_id, ikey, result, "brain_learn_failure")
        return json.dumps(result)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_status(agent_id: str = "") -> str:
        """Show agent identity, group memberships, store stats, and Hive connectivity.

        The response reflects the effective ``agent_id`` after STORY-070.7
        per-call resolution (call param > contextvar/``_meta`` > server default).
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(memory_service.brain_status(s, _pid(), eff_aid), default=str)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def recall_quality_metrics(
        window_seconds: int = 3600,
        project_id: str = "",
        agent_id: str = "",
    ) -> str:
        """Aggregate recall-quality samples over the last *window_seconds* (TAP-2094).

        Returns p50/p95 of ``top_score`` and ``oldest_returned_age_days`` plus
        the empty-recall rate, computed over the in-process ring buffer.
        ``project_id`` defaults to the caller's effective project.

        The ring buffer is process-local and bounded (default 1000 samples
        per project); it resets on restart.
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.recall_quality_metrics(
                s,
                _pid(),
                eff_aid,
                window_seconds=window_seconds,
                target_project_id=project_id,
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_export(
        output_dir: str,
        layout: str = "managed-agents",
        redact: bool = True,
        top_n_per_tier: int = 500,
        project_id: str = "",
        agent_id: str = "",
    ) -> str:
        """Snapshot top-N memories per tier into a Managed Agents folder (TAP-2099).

        One-shot exporter recommended by the TAP-2095 spike (NOT a continuous
        mirror). Writes ``<output_dir>/manifest.json`` plus
        ``<output_dir>/<tier>/<key>.md`` files with redacted values and a
        READ-ONLY banner; entries tagged ``secret`` are skipped wholesale.

        ``project_id`` defaults to the caller's effective project. Refuses to
        overwrite a non-empty ``output_dir``.
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.brain_export(
                s,
                _pid(),
                eff_aid,
                output_dir=output_dir,
                layout=layout,
                redact=redact,
                top_n_per_tier=top_n_per_tier,
                target_project_id=project_id,
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_audit_consumers(
        project_id: str = "",
        since: str = "",
        agent_id: str = "",
    ) -> str:
        """Surface declared-but-silent agents for a project (TAP-2093).

        Joins ``AgentRegistry`` x per-tool call counter to answer "which of my
        declared agents are actually using the brain?". Returns
        ``declared_silent``, ``active``, ``unregistered_active``, ``as_of``,
        and ``window_effective``.

        ``project_id`` defaults to the caller's effective project. ``since``
        accepts an ISO-8601 timestamp but is informational only — the in-process
        counter is cumulative since process start, so the effective window is
        always ``"process_start"`` (reported in ``window_effective``).
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.audit_consumers(
                s,
                _pid(),
                eff_aid,
                target_project_id=project_id,
                since=since,
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_promote_learning(
        key: str,
        signal: str,
        actor: str,
        evidence: str = "",
        agent_id: str = "",
    ) -> str:
        """Approve a learning so it becomes eligible for injection (TAP-5542).

        ``signal`` must be ``"eval"`` (an eval run validated it) or ``"human"``
        (a person signed off). Frequency is not a signal: reinforcing a memory
        raises its confidence but never promotes it, because a learning that is
        merely frequent is not thereby correct.

        ``actor`` is the eval run id or human identifier and is required.
        ``evidence`` is optional free text recorded in the audit log.

        Returns ``{"error": "conflict"}`` when the entry is already approved and
        ``{"error": "not_found"}`` when the key is unknown.
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.brain_promote_learning(
                s,
                _pid(),
                eff_aid,
                key=key,
                signal=signal,
                actor=actor,
                evidence=evidence,
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_demote_learning(
        key: str,
        reason: str,
        agent_id: str = "",
    ) -> str:
        """Withdraw a learning's approval so it stops being injected (TAP-5542).

        ``reason`` is required and stored on the entry. The promotion
        provenance is kept, so an audit can still see which approval was
        withdrawn.
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.brain_demote_learning(
                s,
                _pid(),
                eff_aid,
                key=key,
                reason=reason,
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_recall_tool_paths(
        task_type: str,
        limit: int = 5,
        learning_status: str = "approved",
        min_confidence: float | None = None,
        agent_id: str = "",
    ) -> str:
        """Recall approved tool paths for a task type (TAP-5545).

        Returns only learnings that passed an explicit promotion gate and carry
        a ``fleet:learning`` or ``tool:*`` tag. When nothing approved matches,
        the answer is an empty list — never a fallback to candidates, because a
        caller that asked for validated learnings and silently got unvetted ones
        cannot tell the difference.

        ``learning_status="any"`` opts into candidates for diagnostics;
        ``demoted`` entries are never returned.
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.brain_recall_tool_paths(
                s,
                _pid(),
                eff_aid,
                task_type=task_type,
                limit=limit,
                learning_status=learning_status,
                min_confidence=min_confidence,
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_mission_state_set(
        mission_id: str,
        kind: str,
        value_json: str,
        run_id: str = "",
        agent_id: str = "",
    ) -> str:
        """Park mission-scoped shared state (TAP-5544).

        Lets a fresh worker pick up a mission without inheriting another
        agent's trajectory. ``kind`` is one of ``contract`` / ``findings`` /
        ``knowledge``; ``value_json`` is a JSON-encoded payload. ``run_id``
        narrows the slot to one run within the mission.
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        try:
            value = json.loads(value_json)
        except (TypeError, ValueError) as exc:
            return json.dumps(
                {"error": "invalid_request", "message": f"value_json must be JSON: {exc}"}
            )
        return json.dumps(
            memory_service.brain_mission_state_set(
                s,
                _pid(),
                eff_aid,
                mission_id=mission_id,
                kind=kind,
                value=value,
                run_id=run_id,
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_mission_state_get(
        mission_id: str,
        kind: str,
        run_id: str = "",
        agent_id: str = "",
    ) -> str:
        """Read back mission-scoped shared state (TAP-5544).

        Returns ``{"found": false, "value": null}`` when the mission has not
        written that slot yet — a normal answer for a worker picking up a
        mission, not an error. State belonging to another mission is never
        returned.
        """
        eff_aid = _rpc(agent_id, default=_server_aid)
        s = _resolve(agent_id)
        return json.dumps(
            memory_service.brain_mission_state_get(
                s,
                _pid(),
                eff_aid,
                mission_id=mission_id,
                kind=kind,
                run_id=run_id,
            ),
            default=str,
        )
