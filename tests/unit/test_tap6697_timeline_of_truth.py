"""TAP-6697: timeline of truth — close_validity, live-row status, decay refresh.

Three behaviours are pinned here, each of which was previously "true only on the
path that happened to write the column":

* VAL-05 — one helper closes a row's validity interval, so ``invalid_at`` and
  ``status`` always move together and one audit row records why.
* VAL-03 — the scheduled decay refresh writes down what lazy read-path decay
  recomputes on every recall, closing decayed rows and archiving floor-crossers
  with ``archive_reason='age'``.
* VAL-04 — the demotion sweep's CLI surface reports exactly what
  ``MemoryStore.decay_learnings`` decides, and tier escalation (an entirely
  separate axis) still never touches ``learning_status``.

Every "count is zero" assertion below is paired with a positive control: an
empty result only proves something when the same probe is shown to detect the
non-empty case (corrections-log #1/#3 — an unvalidated empty-equals-pass check
is how the first pass concluded consolidation had never run).
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tapps_brain import _postgres_private_sql as _sql
from tapps_brain.decay import DecayConfig, identify_decay_refresh
from tapps_brain.models import MemoryEntry, MemoryStatus, MemoryTier, _utc_now_iso
from tapps_brain.store import (
    CLOSE_VALIDITY_AUDIT_ACTION,
    TIER_ESCALATE_AUDIT_ACTION,
    MemoryStore,
    _close_validity_updates,
)

_NOW = datetime(2026, 8, 27, tzinfo=UTC)


def _search_sql(**overrides: Any) -> tuple[str, list[Any]]:
    """``build_search_sql`` with its six required filter kwargs defaulted off."""
    kwargs: dict[str, Any] = {
        "memory_group": None,
        "since": None,
        "until": None,
        "time_field": "created_at",
        "memory_class": None,
        "as_of": None,
    }
    kwargs.update(overrides)
    return _sql.build_search_sql(**kwargs)


@pytest.fixture
def store() -> Any:
    """A MemoryStore on the autouse in-memory backend (tests/conftest.py)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        s = MemoryStore(project_root=Path(tmpdir), embedding_provider=None)
        try:
            yield s
        finally:
            s.close()


def _audit_actions(store: MemoryStore, key: str) -> list[dict[str, Any]]:
    """Every audit row for *key*, read from the audit log — never from status.

    Corrections-log #1: mechanism claims ("did the pass run?") are validated
    against the audit log, which records what ran; status columns only record
    what is.

    Goes through the backend's ``query_audit``, not the JSONL file behind
    ``audit_path``. The two are not interchangeable: the in-memory unit-test
    backend keeps a JSONL file, while a run with ``TAPPS_BRAIN_DATABASE_URL`` set
    (CI) writes to the Postgres ``audit_log`` table and has no such file. Reading
    the file directly made every assertion here vacuously true on the Postgres
    backend. ``query_audit`` normalises both into ``event_type`` / ``key`` /
    ``details``; this helper flattens ``details`` up so callers can read
    ``row["reason"]`` on either backend.
    """
    rows: list[dict[str, Any]] = []
    for rec in store._persistence.query_audit(key=key, limit=1000):
        details = rec.get("details") or {}
        flat: dict[str, Any] = {
            **(details if isinstance(details, dict) else {}),
            "action": str(rec.get("event_type", "")),
            "key": str(rec.get("key", "")),
        }
        rows.append(flat)
    return rows


# ---------------------------------------------------------------------------
# VAL-05 — close_validity
# ---------------------------------------------------------------------------


class TestCloseValidityReasonBranches:
    """One helper, three reasons, one status each."""

    @pytest.mark.parametrize(
        ("reason", "expected_status"),
        [
            ("contradiction", MemoryStatus.contradicted),
            ("consolidation", MemoryStatus.contradicted),
            ("supersession", MemoryStatus.superseded),
            ("age", MemoryStatus.stale),
        ],
    )
    def test_reason_maps_to_status_and_sets_invalid_at(
        self, store: MemoryStore, reason: str, expected_status: MemoryStatus
    ) -> None:
        store.save(key=f"k-{reason}", value="a fact that stopped being true", tier="pattern")

        updated = store.close_validity(f"k-{reason}", reason=reason)

        assert updated is not None
        assert updated.status is expected_status
        assert updated.invalid_at is not None, "close_validity must write the closing bound"

    def test_contradiction_branch_sets_the_boolean_in_step(self, store: MemoryStore) -> None:
        """status='contradicted' and contradicted=True are one signal, not two."""
        store.save(key="k-c", value="refuted by a newer write", tier="pattern")

        updated = store.close_validity("k-c", reason="contradiction", detail="lost a conflict")

        assert updated is not None
        assert updated.contradicted is True
        assert updated.contradiction_reason == "lost a conflict"

    def test_age_branch_records_stale_reason_and_date(self, store: MemoryStore) -> None:
        store.save(key="k-a", value="decayed out", tier="context")

        updated = store.close_validity("k-a", reason="age", detail="below stale_threshold")

        assert updated is not None
        assert updated.stale_reason == "below stale_threshold"
        assert updated.stale_date is not None
        # The age branch must NOT borrow the contradiction columns.
        assert updated.contradicted is False

    def test_supersession_branch_links_the_successor(self, store: MemoryStore) -> None:
        store.save(key="old", value="v1", tier="pattern")
        store.save(key="new", value="v2", tier="pattern")

        updated = store.close_validity("old", reason="supersession", superseded_by="new")

        assert updated is not None
        assert updated.superseded_by == "new"
        assert updated.status is MemoryStatus.superseded

    def test_writes_exactly_one_audit_row(self, store: MemoryStore) -> None:
        store.save(key="k-audit", value="x", tier="pattern")

        # Positive control: the probe must be able to see rows at all, or a
        # later "exactly one" assertion would pass on a broken reader.
        before = _audit_actions(store, "k-audit")
        assert any(r["action"] == "save" for r in before), (
            "audit probe cannot see a known-present save row; the zero/one counts "
            "below would be meaningless"
        )

        store.close_validity("k-audit", reason="age")

        closes = [
            r
            for r in _audit_actions(store, "k-audit")
            if r["action"] == CLOSE_VALIDITY_AUDIT_ACTION
        ]
        assert len(closes) == 1
        assert closes[0]["reason"] == "age"

    def test_unknown_reason_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown close_validity reason"):
            _close_validity_updates("whatever", "2026-08-27T00:00:00+00:00")

    def test_missing_key_returns_none(self, store: MemoryStore) -> None:
        assert store.close_validity("no-such-key", reason="age") is None

    def test_revival_round_trip_reopens_status(self, store: MemoryStore) -> None:
        """A closed row that is legitimately re-saved must come back fully live.

        The TAP-5616 revival reset already cleared ``invalid_at`` and the
        contradiction flags. TAP-6697 added ``status`` to the closing bound, so
        the reset has to clear that too — otherwise the row is live on the
        temporal axis and dead on the status axis, which is the exact split the
        helper exists to remove.
        """
        store.save(key="revive-me", value="original", tier="pattern")
        closed = store.close_validity("revive-me", reason="contradiction", detail="was wrong")
        assert closed is not None
        assert closed.status is MemoryStatus.contradicted

        future = (_NOW + timedelta(days=365)).isoformat()
        revived = store.save(key="revive-me", value="corrected", tier="pattern", valid_at=future)

        assert isinstance(revived, MemoryEntry)
        assert revived.status is MemoryStatus.active
        assert revived.invalid_at is None
        assert revived.contradicted is False
        assert revived.stale_reason is None


class TestLiveRowPredicate:
    """VAL-05: the SQL predicate carries the status axis."""

    def test_predicate_source_includes_status_active(self) -> None:
        assert "status = 'active'" in _sql._LIVE_ROW_PREDICATE_SQL

    def test_both_recall_channels_carry_the_clause(self) -> None:
        fts_sql, _ = _search_sql()
        knn_sql, _ = _sql.build_knn_search_sql()
        assert "status = 'active'" in fts_sql
        assert "status = 'active'" in knn_sql
        assert "status = 'active'" in _sql.KNN_SEARCH_SQL

    def test_include_stale_drops_only_the_status_clause(self) -> None:
        """The opt-in widens one axis; expired rows stay excluded."""
        sql, _ = _search_sql(include_stale=True)
        assert "status = 'active'" not in sql
        # Temporal exclusions survive — include_stale is not include_expired.
        assert "invalid_at IS NULL OR invalid_at > now()" in sql
        assert "superseded_by IS NULL" in sql

    def test_include_expired_still_stands_the_whole_predicate_down(self) -> None:
        sql, _ = _search_sql(include_expired=True)
        assert "status = 'active'" not in sql
        assert "invalid_at IS NULL OR invalid_at > now()" not in sql

    def test_predicate_binds_no_parameters(self) -> None:
        """The status clause must not perturb the caller's param tuple."""
        _, params_default = _search_sql()
        _, params_stale = _search_sql(include_stale=True)
        assert params_default == params_stale == []

    def test_include_stale_reaches_the_backend_from_recall(self, monkeypatch: Any) -> None:
        """brain_recall's include_stale must survive all the way to the SQL builder.

        Without the forwarding, tightening the predicate would silently turn the
        public ``include_stale`` flag into a no-op — a passing test suite and a
        broken opt-in.
        """
        from tapps_brain.services import memory_service

        seen: dict[str, Any] = {}

        class _FakeStore:
            def search(self, _query: str, **kwargs: Any) -> list[Any]:
                seen.update(kwargs)
                return []

        memory_service.brain_recall(_FakeStore(), "p", "a", query="anything", include_stale=True)
        assert seen.get("include_stale") is True

        seen.clear()
        memory_service.brain_recall(_FakeStore(), "p", "a", query="anything")
        assert seen.get("include_stale") is False


# ---------------------------------------------------------------------------
# VAL-03 — decay refresh
# ---------------------------------------------------------------------------


def _aged_entry(
    key: str,
    *,
    tier: MemoryTier,
    confidence: float,
    days_ago: float,
    status: MemoryStatus = MemoryStatus.active,
) -> MemoryEntry:
    ts = (_NOW - timedelta(days=days_ago)).isoformat()
    return MemoryEntry(
        key=key,
        value=f"value for {key}",
        tier=tier,
        confidence=confidence,
        created_at=ts,
        updated_at=ts,
        last_accessed=ts,
        status=status,
    )


class TestIdentifyDecayRefresh:
    """The pure decision half: same list the dry run reports and apply writes."""

    def test_fresh_row_is_left_alone(self) -> None:
        cfg = DecayConfig()
        fresh = _aged_entry("fresh", tier=MemoryTier.context, confidence=0.9, days_ago=0.5)

        assert identify_decay_refresh([fresh], cfg, now=_NOW) == []

    def test_decayed_context_row_is_closed(self) -> None:
        """Positive control for the zero-count assertion above."""
        cfg = DecayConfig()
        # context half-life is 14d; 28 days is two half-lives -> 0.9 * 0.25.
        old = _aged_entry("old-context", tier=MemoryTier.context, confidence=0.9, days_ago=28)

        actions = identify_decay_refresh([old], cfg, now=_NOW)

        assert [a.action for a in actions] == ["close"]
        assert actions[0].key == "old-context"
        assert actions[0].effective_confidence < cfg.stale_threshold

    def test_floor_crosser_is_archived_not_closed(self) -> None:
        cfg = DecayConfig()
        dead = _aged_entry("very-old", tier=MemoryTier.context, confidence=0.9, days_ago=400)

        actions = identify_decay_refresh([dead], cfg, now=_NOW)

        assert [a.action for a in actions] == ["archive"]
        assert actions[0].days_at_floor is not None

    def test_floor_is_checked_before_stale(self) -> None:
        """A row deep below the floor archives outright.

        GC never auto-archives ``status='stale'`` rows (they await review), so
        closing first and archiving later would strand them permanently.
        """
        cfg = DecayConfig()
        dead = _aged_entry("dead", tier=MemoryTier.context, confidence=0.9, days_ago=400)

        actions = identify_decay_refresh([dead], cfg, now=_NOW)

        assert all(a.action != "close" for a in actions)

    def test_already_closed_rows_are_skipped_so_the_pass_is_idempotent(self) -> None:
        cfg = DecayConfig()
        closed = _aged_entry(
            "closed",
            tier=MemoryTier.context,
            confidence=0.9,
            days_ago=28,
            status=MemoryStatus.stale,
        )

        assert identify_decay_refresh([closed], cfg, now=_NOW) == []

    def test_superseded_rows_are_skipped(self) -> None:
        """The supersession chain must stay inspectable for undo."""
        cfg = DecayConfig()
        entry = _aged_entry("src", tier=MemoryTier.context, confidence=0.9, days_ago=28)
        entry = entry.model_copy(update={"superseded_by": "merged-key"})

        assert identify_decay_refresh([entry], cfg, now=_NOW) == []

    def test_architectural_tier_outlives_context_tier(self) -> None:
        """Tier half-life is honoured, not a flat age cut-off."""
        cfg = DecayConfig()
        arch = _aged_entry("arch", tier=MemoryTier.architectural, confidence=0.9, days_ago=28)
        ctx = _aged_entry("ctx", tier=MemoryTier.context, confidence=0.9, days_ago=28)

        actions = identify_decay_refresh([arch, ctx], cfg, now=_NOW)

        assert [a.key for a in actions] == ["ctx"]


class TestRefreshDecayStore:
    """The applying half, against a live store."""

    def _seed_decayed(self, store: MemoryStore, key: str, *, days_ago: float) -> None:
        store.save(key=key, value=f"stale content for {key}", tier="context")
        ts = (datetime.now(tz=UTC) - timedelta(days=days_ago)).isoformat()
        store.update_fields(key, created_at=ts, updated_at=ts, last_accessed=ts, confidence=0.35)

    def test_dry_run_writes_nothing(self, store: MemoryStore) -> None:
        self._seed_decayed(store, "d1", days_ago=60)

        result = store.refresh_decay(dry_run=True)

        assert result["dry_run"] is True
        assert result["would_close"] + result["would_archive"] >= 1
        assert result["rows_after"] == result["rows_before"]
        assert store.get("d1").status is MemoryStatus.active

    def test_dry_run_and_apply_agree_on_the_key_set(self, store: MemoryStore) -> None:
        self._seed_decayed(store, "d2", days_ago=60)
        preview = store.refresh_decay(dry_run=True)

        applied = store.refresh_decay(dry_run=False)

        assert applied["closed"] + applied["archived"] == (
            preview["would_close"] + preview["would_archive"]
        )

    def test_apply_closes_the_row_and_audits_it(self, store: MemoryStore) -> None:
        self._seed_decayed(store, "d3", days_ago=60)

        result = store.refresh_decay(dry_run=False)

        assert result["closed"] >= 1
        entry = store.get("d3")
        assert entry.status is MemoryStatus.stale
        assert entry.invalid_at is not None
        closes = [
            r for r in _audit_actions(store, "d3") if r["action"] == CLOSE_VALIDITY_AUDIT_ACTION
        ]
        assert len(closes) == 1
        assert closes[0]["reason"] == "age"

    def test_row_conservation_holds(self, store: MemoryStore) -> None:
        """VAL-03: rows_before == rows_after + archived_delta."""
        for i in range(3):
            self._seed_decayed(store, f"cons-{i}", days_ago=60)
        store.save(key="live", value="a fresh, useful fact", tier="architectural")

        result = store.refresh_decay(dry_run=False)

        assert result["rows_before"] == result["rows_after"] + result["archived_delta"]

    def test_archived_rows_carry_reason_age_in_the_payload(self, store: MemoryStore) -> None:
        store.save(key="floor", value="ancient and worthless", tier="context")
        ts = (datetime.now(tz=UTC) - timedelta(days=900)).isoformat()
        store.update_fields(
            "floor", created_at=ts, updated_at=ts, last_accessed=ts, confidence=0.05
        )

        result = store.refresh_decay(dry_run=False)

        assert result["archived"] >= 1
        archived = store._persistence.list_archive(limit=50)
        payloads = [row["payload"] for row in archived if row["key"] == "floor"]
        assert payloads, "floor-crossing row was not written to gc_archive"
        assert payloads[0]["archive_reason"] == "age"
        assert store.get("floor") is None, "archived row must leave the live table"

    def test_refresh_is_idempotent(self, store: MemoryStore) -> None:
        self._seed_decayed(store, "d4", days_ago=60)
        first = store.refresh_decay(dry_run=False)
        assert first["closed"] + first["archived"] >= 1

        second = store.refresh_decay(dry_run=False)

        assert second["closed"] == 0
        assert second["archived"] == 0

    def test_fresh_rows_survive(self, store: MemoryStore) -> None:
        store.save(key="keeper", value="a decision made yesterday", tier="architectural")

        store.refresh_decay(dry_run=False)

        assert store.get("keeper").status is MemoryStatus.active


# ---------------------------------------------------------------------------
# VAL-04 — demotion sweep and the tier/trust axis separation
# ---------------------------------------------------------------------------


class TestDemotionSweepWrapper:
    def test_cli_wrapper_reports_what_the_store_decided(self, store: MemoryStore) -> None:
        """The CLI adds a surface, not a second implementation."""
        from typer.testing import CliRunner

        from tapps_brain.cli._common import maintenance_app

        expected = store.decay_learnings(dry_run=True)

        captured: dict[str, Any] = {}

        def _fake_get_store(_project_dir: Any) -> Any:
            captured["called"] = True
            return store

        import tapps_brain.cli.maintenance as _maint

        original = _maint._get_store
        _maint._get_store = _fake_get_store  # type: ignore[assignment]
        try:
            result = CliRunner().invoke(
                maintenance_app, ["demote-contradicted", "--dry-run", "--json"]
            )
        finally:
            _maint._get_store = original  # type: ignore[assignment]

        assert result.exit_code == 0, result.output
        assert captured["called"] is True
        payload = json.loads(result.stdout)
        assert payload["demoted_keys"] == expected["demoted_keys"]
        assert payload["reason_counts"] == expected["reason_counts"]
        assert payload["dry_run"] is True


class TestTierEscalationDoesNotTouchTrust:
    def test_audit_action_is_renamed_to_tier_escalate(self) -> None:
        """Ruling 13: the tier axis stops sharing a name with the trust axis."""
        assert TIER_ESCALATE_AUDIT_ACTION == "tier_escalate"
        source = Path("src/tapps_brain/store.py").read_text(encoding="utf-8")
        # Anchor on the call, not on prose: the module comment explaining the
        # rename legitimately quotes the old string (corrections-log #4 — count
        # declarations, not mentions).
        assert "action=TIER_ESCALATE_AUDIT_ACTION," in source
        assert 'append_audit(\n                action="promote",' not in source
        # The trust-axis events keep their own names.
        assert 'action="learning_promote"' in source
        assert 'action="learning_demote"' in source

    def test_pre_rename_audit_rows_are_not_rewritten(self, store: MemoryStore) -> None:
        """Old rows keep action='promote' — the rename is additive."""
        store.save(key="legacy", value="escalated long ago", tier="context")
        store._persistence.append_audit(
            action="promote", key="legacy", extra={"from_tier": "context", "to_tier": "pattern"}
        )

        store.refresh_decay(dry_run=True)

        legacy = [r for r in _audit_actions(store, "legacy") if r["action"] == "promote"]
        assert len(legacy) == 1, "a pre-rename audit row must survive untouched"

    def test_a_real_escalation_emits_tier_escalate_and_no_trust_change(self) -> None:
        """Drive an actual tier escalation and read the audit row it wrote.

        A source grep proves the literal changed; this proves the *event* changed
        and, in the same breath, that ``learning_status`` came through untouched
        (VAL-04 clause 3). Both facts are read from ``audit_log``/the row, not
        inferred from the code (corrections-log #1).
        """
        from tapps_brain.profile import get_builtin_profile

        profile = get_builtin_profile("research-knowledge")
        layer = next(layer_ for layer_ in profile.layers if layer_.promotion_to is not None)
        threshold = layer.promotion_threshold
        assert threshold is not None

        with tempfile.TemporaryDirectory() as tmpdir:
            s = MemoryStore(Path(tmpdir), profile=profile, embedding_provider=None)
            try:
                s.save(key="climber", value="a finding cited over and over", tier=layer.name)
                old_ts = datetime.now(tz=UTC) - timedelta(days=threshold.min_age_days + 5)
                s.update_fields(
                    "climber",
                    created_at=old_ts.isoformat(),
                    access_count=threshold.min_access_count + 5,
                    confidence=0.99,
                )
                before = s.get("climber")
                assert before is not None
                trust_before = before.learning_status

                s.reinforce("climber")

                escalations = [
                    r
                    for r in _audit_actions(s, "climber")
                    if r["action"] == TIER_ESCALATE_AUDIT_ACTION
                ]
                assert escalations, (
                    "no tier_escalate audit row — the fixture did not actually "
                    "escalate, so this test would prove nothing"
                )
                assert escalations[0]["to_tier"] == layer.promotion_to
                # Positive control for the "no promote row" assertion below.
                assert not [r for r in _audit_actions(s, "climber") if r["action"] == "promote"]
                after = s.get("climber")
                assert after is not None
                assert str(after.tier) == layer.promotion_to, "tier axis did move"
                assert after.learning_status is trust_before, "trust axis must not move"
                assert after.promotion_signal is None
                assert after.promoted_by is None
            finally:
                s.close()

    def test_tier_escalation_leaves_learning_status_alone(self, store: MemoryStore) -> None:
        """A fixture row proves the two axes never cross.

        Tier escalation moves ``tier``; the gated-learning contract says only an
        explicit ``eval``/``human`` signal may move ``learning_status``. If a
        reinforcement-driven escalation could also approve a learning, frequency
        would approve by the back door (KB-1.4).
        """
        from tapps_brain.models import LearningStatus

        store.save(key="climber", value="a pattern that gets used a lot", tier="context")
        before = store.get("climber")
        assert before.learning_status is LearningStatus.candidate

        for _ in range(25):
            store.reinforce("climber")

        after = store.get("climber")
        assert after.learning_status is LearningStatus.candidate, (
            "tier escalation / reinforcement must never move the trust axis"
        )
        assert after.promotion_signal is None
        assert after.promoted_by is None


# ---------------------------------------------------------------------------
# Round-2 regressions — the two defects an independent verifier refuted
# ---------------------------------------------------------------------------


def _live_but_closed(store: MemoryStore, now: datetime) -> list[str]:
    """Keys satisfying ``invalid_at <= now() AND status = 'active'``.

    The fixture-store equivalent of VAL-05's live-DB probe. Any key here is a row
    whose two liveness axes disagree: the temporal axis says closed, the status
    axis says live. The invariant is that this list is always empty, whichever
    write path closed the row.
    """
    from tapps_brain.models import _parse_iso

    offenders: list[str] = []
    # The in-process mirror of ``private_memories`` — the same set
    # ``decay_learnings`` sweeps, so the probe sees every row the store holds.
    for entry in list(store._entries.values()):
        if entry.invalid_at is None:
            continue
        if _parse_iso(entry.invalid_at) > now:
            continue
        if entry.status is MemoryStatus.active:
            offenders.append(entry.key)
    return offenders


class TestSupersedeGoesThroughTheCloser:
    """VAL-05(b)/(c): ``supersede()`` must not be a second ``invalid_at`` writer.

    ``supersede()`` used to build its own update dict — ``invalid_at`` +
    ``superseded_by`` + ``updated_at``, no ``status``. A row it closed therefore
    satisfied ``invalid_at <= now() AND status='active'`` *permanently*, which
    falsifies VAL-05 clause (c) by construction rather than by race. Routing it
    through :func:`_close_validity_updates` is what makes "one closer, no
    exceptions" true.
    """

    def test_superseded_row_is_not_left_active(self, store: MemoryStore) -> None:
        # Positive control: the probe must be able to see an offender at all,
        # or the "== []" assertion below would pass on a blind reader
        # (corrections-log #1/#3).
        store.save(key="planted", value="closed by hand, status untouched", tier="pattern")
        store.update_fields("planted", invalid_at=_utc_now_iso())
        now = datetime.now(tz=UTC)
        assert _live_but_closed(store, now) == ["planted"], (
            "probe cannot detect a known-present offender; the assertion below would be vacuous"
        )
        # Repair the plant through the closer so it stops being an offender.
        store.close_validity("planted", reason="supersession")
        assert _live_but_closed(store, datetime.now(tz=UTC)) == []

        store.save(key="fact", value="the original claim", tier="pattern")
        new_entry = store.supersede("fact", "the corrected claim")

        old = store.get("fact")
        assert old is not None
        assert old.invalid_at is not None
        assert old.superseded_by == new_entry.key
        assert old.status is MemoryStatus.superseded, (
            "supersede() must stamp status atomically with invalid_at"
        )
        assert _live_but_closed(store, datetime.now(tz=UTC)) == []

    def test_supersede_writes_one_close_validity_audit_row(self, store: MemoryStore) -> None:
        """A closure is provable from ``audit_log`` on this path too."""
        store.save(key="fact2", value="v1", tier="pattern")
        store.supersede("fact2", "v2")

        closes = [
            r for r in _audit_actions(store, "fact2") if r["action"] == CLOSE_VALIDITY_AUDIT_ACTION
        ]
        assert len(closes) == 1
        assert closes[0]["reason"] == "supersession"


class TestContradictionDemotionIgnoresPriorStatus:
    """VAL-04(a): a contradicted row is demoted whatever its prior status.

    The rule used to sit under ``if status == LearningStatus.approved:``, which
    then unconditionally ``continue``d. Every contradicted row in the live brain
    is a ``candidate``, so the rule fired on nothing: a contradicted candidate
    that is recent and above the confidence floor is immune to the other two
    rules as well. SC-2 and VAL-04(a) both state the contract with no status
    precondition.
    """

    def _seed_contradicted_candidate(self, store: MemoryStore, key: str) -> None:
        """A contradicted candidate immune to the decayed/unvalidated rules."""
        from tapps_brain.models import LearningStatus

        store.save(key=key, value="a claim later refuted", tier="architectural")
        store.close_validity(key, reason="contradiction", detail="refuted by TAP-6697")
        store.update_fields(key, confidence=0.99, updated_at=_utc_now_iso())
        entry = store.get(key)
        assert entry is not None
        assert entry.learning_status is LearningStatus.candidate
        assert entry.contradicted is True

    def test_contradicted_candidate_is_identified_for_demotion(self, store: MemoryStore) -> None:
        from tapps_brain.decay import DEMOTE_REASON_CONTRADICTED

        self._seed_contradicted_candidate(store, "cand-contradicted")

        plan = store.decay_learnings(dry_run=True)

        assert "cand-contradicted" in plan["demoted_keys"]
        assert plan["reason_counts"].get(DEMOTE_REASON_CONTRADICTED) == 1

    def test_contradicted_candidate_is_demoted_and_audited(self, store: MemoryStore) -> None:
        from tapps_brain.models import LearningStatus

        self._seed_contradicted_candidate(store, "cand-demote-me")

        # Positive control for the audit probe.
        assert any(r["action"] == "save" for r in _audit_actions(store, "cand-demote-me"))

        store.decay_learnings(dry_run=False)

        after = store.get("cand-demote-me")
        assert after is not None
        assert after.learning_status is LearningStatus.demoted
        demotes = [
            r for r in _audit_actions(store, "cand-demote-me") if r["action"] == "learning_demote"
        ]
        assert len(demotes) == 1

    def test_approved_contradicted_row_still_demotes(self, store: MemoryStore) -> None:
        """Widening the rule must not lose the case it already covered."""
        from tapps_brain.models import LearningStatus

        store.save(key="appr", value="an approved claim later refuted", tier="architectural")
        store.update_fields(
            "appr",
            learning_status=LearningStatus.approved,
            promotion_signal="human",
            promoted_by="operator",
            promoted_at=_utc_now_iso(),
            confidence=0.99,
        )
        store.close_validity("appr", reason="contradiction", detail="refuted")
        store.update_fields("appr", updated_at=_utc_now_iso())

        store.decay_learnings(dry_run=False)

        after = store.get("appr")
        assert after is not None
        assert after.learning_status is LearningStatus.demoted

    def test_uncontradicted_recent_candidate_is_left_alone(self, store: MemoryStore) -> None:
        """The widened rule must not demote everything it walks past."""
        from tapps_brain.models import LearningStatus

        store.save(key="healthy", value="a fresh, uncontradicted claim", tier="architectural")
        store.update_fields("healthy", confidence=0.99, updated_at=_utc_now_iso())

        store.decay_learnings(dry_run=False)

        after = store.get("healthy")
        assert after is not None
        assert after.learning_status is LearningStatus.candidate
