"""Recall-set provenance: ``recall_digest`` + ``memory_versions`` (TAP-6583).

tapps-brain is the only component of an AgentForge system prompt that is not on
disk and not git-versioned, so it is the only one without a diffable handle.
These tests pin the handle's contract: it names the set that reached the prompt,
it is stable, it moves when the content moves, and it ignores row order.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from tapps_brain.models import RecallResult
from tapps_brain.recall import RecallConfig, RecallOrchestrator
from tapps_brain.recall_digest import compute_recall_digest, memory_version
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture()
def store(tmp_path: Path) -> Generator[MemoryStore, None, None]:
    """A store whose entries all match one query, so recall returns several."""
    s = MemoryStore(tmp_path)
    for idx, body in enumerate(
        (
            "The deploy pipeline builds the wheel then pushes the image",
            "The deploy pipeline runs migrations as a one-shot sidecar",
            "The deploy pipeline restarts the http container last",
        ),
        start=1,
    ):
        s.save(key=f"deploy-note-{idx}", value=body, tier="architectural", source="human")
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Pure digest function
# ---------------------------------------------------------------------------


class TestComputeRecallDigest:
    def test_empty_set_matches_field_defaults(self) -> None:
        digest, versions = compute_recall_digest([])
        assert digest == ""
        assert versions == []
        assert digest == RecallResult().recall_digest
        assert versions == RecallResult().memory_versions

    def test_versions_pair_key_with_content_hash(self) -> None:
        _, versions = compute_recall_digest([{"key": "k1", "value": "hello"}])
        assert [v.key for v in versions] == ["k1"]
        assert versions[0].version == hashlib.sha256(b"hello").hexdigest()[:16]

    def test_row_order_does_not_change_the_digest(self) -> None:
        rows = [
            {"key": "a", "value": "alpha"},
            {"key": "b", "value": "beta"},
            {"key": "c", "value": "gamma"},
        ]
        forward, _ = compute_recall_digest(rows)
        reversed_digest, _ = compute_recall_digest(list(reversed(rows)))
        shuffled, _ = compute_recall_digest([rows[1], rows[2], rows[0]])
        assert forward == reversed_digest == shuffled

    def test_memory_versions_keep_injected_order(self) -> None:
        rows = [{"key": "b", "value": "beta"}, {"key": "a", "value": "alpha"}]
        _, versions = compute_recall_digest(rows)
        assert [v.key for v in versions] == ["b", "a"]

    def test_changed_content_changes_the_digest(self) -> None:
        before, _ = compute_recall_digest([{"key": "a", "value": "alpha"}])
        after, _ = compute_recall_digest([{"key": "a", "value": "alpha!"}])
        assert before != after

    def test_dropping_a_member_changes_the_digest(self) -> None:
        both, _ = compute_recall_digest(
            [{"key": "a", "value": "alpha"}, {"key": "b", "value": "beta"}]
        )
        one, _ = compute_recall_digest([{"key": "a", "value": "alpha"}])
        assert both != one

    def test_key_is_part_of_the_digest(self) -> None:
        """Same content under a different key is a different set."""
        first, _ = compute_recall_digest([{"key": "a", "value": "same"}])
        second, _ = compute_recall_digest([{"key": "b", "value": "same"}])
        assert first != second

    def test_digest_is_plain_sha256_not_the_keyed_hmac(self) -> None:
        """Reproducible on any machine: no ``~/.tapps-brain/integrity.key``.

        The HMAC in ``tapps_brain.integrity`` answers a different question
        (tamper detection with a per-installation secret) and must not leak
        into this content address.
        """
        digest, _ = compute_recall_digest([{"key": "a", "value": "alpha"}])
        expected_version = hashlib.sha256(b"alpha").hexdigest()[:16]
        canonical = f'[["a","{expected_version}"]]'.encode()
        assert digest == hashlib.sha256(canonical).hexdigest()
        assert len(digest) == 64


class TestMemoryVersion:
    def test_version_is_deterministic(self) -> None:
        assert memory_version("x") == memory_version("x")

    def test_version_is_unicode_safe(self) -> None:
        assert memory_version("café") == hashlib.sha256("café".encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Through RecallOrchestrator — the set that actually reaches the prompt
# ---------------------------------------------------------------------------


class TestRecallResultDigest:
    def test_digest_is_stable_across_two_calls(self, store: MemoryStore) -> None:
        orch = RecallOrchestrator(store)
        first = orch.recall("deploy pipeline")
        second = orch.recall("deploy pipeline")
        assert first.memory_count > 0
        assert first.recall_digest != ""
        assert first.recall_digest == second.recall_digest
        assert first.memory_versions == second.memory_versions

    def test_versions_cover_exactly_the_returned_memories(self, store: MemoryStore) -> None:
        result = RecallOrchestrator(store).recall("deploy pipeline")
        assert [v.key for v in result.memory_versions] == [str(m["key"]) for m in result.memories]
        assert [v.version for v in result.memory_versions] == [
            memory_version(str(m["value"])) for m in result.memories
        ]

    def test_digest_changes_when_a_recalled_memory_changes(self, store: MemoryStore) -> None:
        orch = RecallOrchestrator(store)
        before = orch.recall("deploy pipeline")
        changed_key = str(before.memories[0]["key"])
        store.save(
            key=changed_key,
            value="The deploy pipeline now builds from a tag worktree",
            tier="architectural",
            source="human",
        )
        after = orch.recall("deploy pipeline")
        assert changed_key in {str(m["key"]) for m in after.memories}
        assert after.recall_digest != before.recall_digest

    def test_digest_describes_the_prompt_not_the_candidate_pool(self, store: MemoryStore) -> None:
        """Force the token budget to drop a memory; the digest must move."""
        orch = RecallOrchestrator(store)
        roomy = orch.recall("deploy pipeline", max_tokens=3000)
        assert roomy.memory_count > 1
        assert roomy.truncated is False

        squeezed = orch.recall("deploy pipeline", max_tokens=40)
        assert squeezed.truncated is True
        assert squeezed.memory_count < roomy.memory_count
        assert squeezed.recall_digest != roomy.recall_digest
        assert len(squeezed.memory_versions) == squeezed.memory_count

    def test_empty_recall_leaves_the_fields_empty(self, store: MemoryStore) -> None:
        result = RecallOrchestrator(store, config=RecallConfig(engagement_level="low")).recall(
            "deploy pipeline"
        )
        assert result.memory_count == 0
        assert result.recall_digest == ""
        assert result.memory_versions == []

    def test_fields_are_additive_for_pre_change_callers(self, store: MemoryStore) -> None:
        """A caller that ignores the new fields sees an unchanged payload."""
        result = RecallOrchestrator(store).recall("deploy pipeline")
        legacy = result.model_dump(exclude={"recall_digest", "memory_versions"})
        assert set(legacy) == set(RecallResult().model_dump()) - {
            "recall_digest",
            "memory_versions",
        }
        assert legacy["memory_section"].startswith("### Project Memory")
