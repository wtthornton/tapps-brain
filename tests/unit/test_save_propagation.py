"""Unit tests for ``_save_propagation.py`` — Hive fan-out helpers.

Covers :func:`propagate_group_save` and :func:`publish_to_experts` across the
branches the acceptance criteria call out: Hive offline (``None``), Hive
raises mid-call, and ``agent_scope == "private"`` (no propagation).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from tapps_brain._save_propagation import propagate_group_save, publish_to_experts
from tapps_brain.models import MemoryEntry, MemoryTier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    key: str = "mem-key",
    value: str = "mem value",
    tier: MemoryTier = MemoryTier.pattern,
    agent_scope: str = "private",
    tags: list[str] | None = None,
) -> MemoryEntry:
    return MemoryEntry(
        key=key,
        value=value,
        tier=tier,
        agent_scope=agent_scope,
        tags=tags or [],
    )


def _hive() -> MagicMock:
    """Return a fresh MagicMock acting as a HiveBackend."""
    return MagicMock()


# ---------------------------------------------------------------------------
# propagate_group_save — no-op cases
# ---------------------------------------------------------------------------


class TestPropagateGroupSaveNoOp:
    """Cases where propagate_group_save performs no Hive writes."""

    def test_none_hive_is_noop(self) -> None:
        entry = _entry(agent_scope="group")
        # Should not raise even though hive_store is None
        propagate_group_save(
            entry=entry,
            agent_scope="group",
            groups=["team-a"],
            hive_store=None,
        )

    def test_empty_groups_is_noop(self) -> None:
        hive = _hive()
        entry = _entry(agent_scope="group")
        propagate_group_save(
            entry=entry,
            agent_scope="group",
            groups=[],
            hive_store=hive,
        )
        hive.save.assert_not_called()

    def test_private_scope_is_noop(self) -> None:
        hive = _hive()
        entry = _entry(agent_scope="private")
        propagate_group_save(
            entry=entry,
            agent_scope="private",
            groups=["team-a"],
            hive_store=hive,
        )
        hive.save.assert_not_called()

    def test_domain_scope_is_noop(self) -> None:
        hive = _hive()
        entry = _entry(agent_scope="domain")
        propagate_group_save(
            entry=entry,
            agent_scope="domain",
            groups=["team-a"],
            hive_store=hive,
        )
        hive.save.assert_not_called()

    def test_hive_scope_is_noop(self) -> None:
        hive = _hive()
        entry = _entry(agent_scope="hive")
        propagate_group_save(
            entry=entry,
            agent_scope="hive",
            groups=["team-a"],
            hive_store=hive,
        )
        hive.save.assert_not_called()


# ---------------------------------------------------------------------------
# propagate_group_save — fan-out to all groups
# ---------------------------------------------------------------------------


class TestPropagateGroupSaveFanOut:
    """``agent_scope == "group"`` fans out to every declared group."""

    def test_single_group_fanout(self) -> None:
        hive = _hive()
        entry = _entry(key="k", value="v", tier=MemoryTier.pattern, agent_scope="group")
        propagate_group_save(
            entry=entry,
            agent_scope="group",
            groups=["team-a"],
            hive_store=hive,
        )
        hive.save.assert_called_once()
        _, kwargs = hive.save.call_args
        assert kwargs["namespace"] == "team-a"
        assert kwargs["key"] == "k"
        assert kwargs["value"] == "v"

    def test_multiple_groups_fanout(self) -> None:
        hive = _hive()
        entry = _entry(agent_scope="group", tags=["t1"])
        propagate_group_save(
            entry=entry,
            agent_scope="group",
            groups=["alpha", "beta", "gamma"],
            hive_store=hive,
        )
        assert hive.save.call_count == 3
        namespaces = [call.kwargs["namespace"] for call in hive.save.call_args_list]
        assert set(namespaces) == {"alpha", "beta", "gamma"}

    def test_named_group_scope_deferred_to_propagation_engine(self) -> None:
        """``group:<name>`` is owned by PropagationEngine — no double-write here."""
        hive = _hive()
        entry = _entry(agent_scope="group:beta")
        propagate_group_save(
            entry=entry,
            agent_scope="group:beta",
            groups=["alpha", "beta"],
            hive_store=hive,
        )
        hive.save.assert_not_called()

    def test_tags_forwarded(self) -> None:
        hive = _hive()
        entry = _entry(agent_scope="group", tags=["release", "critical"])
        propagate_group_save(
            entry=entry,
            agent_scope="group",
            groups=["team-a"],
            hive_store=hive,
        )
        _, kwargs = hive.save.call_args
        assert "release" in kwargs["tags"]
        assert "critical" in kwargs["tags"]


# ---------------------------------------------------------------------------
# propagate_group_save — Hive raises mid-call
# ---------------------------------------------------------------------------


class TestPropagateGroupSaveExceptionSwallowed:
    """Hive errors are logged as warnings and never re-raised."""

    def test_hive_exception_is_swallowed(self) -> None:
        hive = _hive()
        hive.save.side_effect = RuntimeError("network timeout")
        entry = _entry(agent_scope="group")

        # Must not raise
        propagate_group_save(
            entry=entry,
            agent_scope="group",
            groups=["team-a"],
            hive_store=hive,
        )

    def test_partial_failure_continues(self) -> None:
        """When the first group write fails, the second group is still attempted."""
        hive = _hive()
        hive.save.side_effect = [RuntimeError("first fails"), None]
        entry = _entry(agent_scope="group")

        propagate_group_save(
            entry=entry,
            agent_scope="group",
            groups=["alpha", "beta"],
            hive_store=hive,
        )

        assert hive.save.call_count == 2


# ---------------------------------------------------------------------------
# publish_to_experts — no-op cases
# ---------------------------------------------------------------------------


class TestPublishToExpertsNoOp:
    """Cases where publish_to_experts writes nothing to Hive."""

    def test_auto_publish_false_is_noop(self) -> None:
        hive = _hive()
        entry = _entry(tier=MemoryTier.architectural, agent_scope="private")
        publish_to_experts(
            entry=entry,
            tier="architectural",
            agent_scope="private",
            expert_domains=["python"],
            hive_store=hive,
            auto_publish=False,
        )
        hive.save.assert_not_called()

    def test_none_hive_is_noop(self) -> None:
        entry = _entry(tier=MemoryTier.architectural, agent_scope="private")
        publish_to_experts(
            entry=entry,
            tier="architectural",
            agent_scope="private",
            expert_domains=["python"],
            hive_store=None,
            auto_publish=True,
        )

    def test_empty_expert_domains_is_noop(self) -> None:
        hive = _hive()
        entry = _entry(tier=MemoryTier.architectural, agent_scope="private")
        publish_to_experts(
            entry=entry,
            tier="architectural",
            agent_scope="private",
            expert_domains=[],
            hive_store=hive,
            auto_publish=True,
        )
        hive.save.assert_not_called()

    def test_context_tier_is_noop(self) -> None:
        """Only architectural and pattern tiers trigger expert auto-publish."""
        hive = _hive()
        entry = _entry(tier=MemoryTier.context, agent_scope="private")
        publish_to_experts(
            entry=entry,
            tier="context",
            agent_scope="private",
            expert_domains=["python"],
            hive_store=hive,
            auto_publish=True,
        )
        hive.save.assert_not_called()

    def test_procedural_tier_is_noop(self) -> None:
        hive = _hive()
        entry = _entry(tier=MemoryTier.procedural, agent_scope="private")
        publish_to_experts(
            entry=entry,
            tier="procedural",
            agent_scope="private",
            expert_domains=["python"],
            hive_store=hive,
            auto_publish=True,
        )
        hive.save.assert_not_called()

    def test_domain_scope_is_noop(self) -> None:
        """Only ``private`` scope triggers expert auto-publish."""
        hive = _hive()
        entry = _entry(tier=MemoryTier.architectural, agent_scope="domain")
        publish_to_experts(
            entry=entry,
            tier="architectural",
            agent_scope="domain",
            expert_domains=["python"],
            hive_store=hive,
            auto_publish=True,
        )
        hive.save.assert_not_called()

    def test_hive_scope_is_noop(self) -> None:
        hive = _hive()
        entry = _entry(tier=MemoryTier.architectural, agent_scope="hive")
        publish_to_experts(
            entry=entry,
            tier="architectural",
            agent_scope="hive",
            expert_domains=["css"],
            hive_store=hive,
            auto_publish=True,
        )
        hive.save.assert_not_called()


# ---------------------------------------------------------------------------
# publish_to_experts — happy path
# ---------------------------------------------------------------------------


class TestPublishToExpertsHappyPath:
    """Architectural / pattern private entries are published to the universal namespace."""

    def test_architectural_entry_published(self) -> None:
        hive = _hive()
        entry = _entry(
            key="arch-key",
            value="architecture decision",
            tier=MemoryTier.architectural,
            agent_scope="private",
            tags=["adr"],
        )
        publish_to_experts(
            entry=entry,
            tier="architectural",
            agent_scope="private",
            expert_domains=["python"],
            hive_store=hive,
            auto_publish=True,
        )

        hive.save.assert_called_once()
        _, kwargs = hive.save.call_args
        assert kwargs["namespace"] == "universal"
        assert kwargs["key"] == "arch-key"
        assert kwargs["value"] == "architecture decision"

    def test_pattern_entry_published(self) -> None:
        hive = _hive()
        entry = _entry(tier=MemoryTier.pattern, agent_scope="private")
        publish_to_experts(
            entry=entry,
            tier="pattern",
            agent_scope="private",
            expert_domains=["react"],
            hive_store=hive,
            auto_publish=True,
        )
        hive.save.assert_called_once()
        _, kwargs = hive.save.call_args
        assert kwargs["namespace"] == "universal"

    def test_expert_tags_appended(self) -> None:
        """``expert:<domain>`` tags are appended to existing entry tags."""
        hive = _hive()
        entry = _entry(
            tier=MemoryTier.architectural,
            agent_scope="private",
            tags=["existing-tag"],
        )
        publish_to_experts(
            entry=entry,
            tier="architectural",
            agent_scope="private",
            expert_domains=["css", "react"],
            hive_store=hive,
            auto_publish=True,
        )

        _, kwargs = hive.save.call_args
        tags = kwargs["tags"]
        assert "expert:css" in tags
        assert "expert:react" in tags
        assert "existing-tag" in tags

    def test_no_existing_tags(self) -> None:
        """Empty entry tags still get the expert tags appended."""
        hive = _hive()
        entry = _entry(tier=MemoryTier.pattern, agent_scope="private", tags=[])
        publish_to_experts(
            entry=entry,
            tier="pattern",
            agent_scope="private",
            expert_domains=["python"],
            hive_store=hive,
            auto_publish=True,
        )

        _, kwargs = hive.save.call_args
        assert kwargs["tags"] == ["expert:python"]


# ---------------------------------------------------------------------------
# publish_to_experts — Hive raises mid-call
# ---------------------------------------------------------------------------


class TestPublishToExpertsExceptionSwallowed:
    """Hive errors during expert publish are swallowed, not re-raised."""

    def test_hive_exception_does_not_propagate(self) -> None:
        hive = _hive()
        hive.save.side_effect = ConnectionError("collector down")
        entry = _entry(tier=MemoryTier.architectural, agent_scope="private")

        # Must not raise
        publish_to_experts(
            entry=entry,
            tier="architectural",
            agent_scope="private",
            expert_domains=["python"],
            hive_store=hive,
            auto_publish=True,
        )

        hive.save.assert_called_once()
