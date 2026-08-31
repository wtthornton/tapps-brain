"""Group-shared memories must be admitted by the recall predicate (TAP-6695, Ruling 16).

``/v1/recall`` scopes candidates by ``(project_id, agent_id)`` **in SQL** and then
applies ``filter_tags`` in Python.  A ``scope:group:<name>`` tag on that path can
therefore only ever *narrow* an already-agent-scoped pool — it can never widen
it, so group sharing could not be made to work by tagging alone.  The fix widens
the agent half of the recall predicate itself, before any Python filter runs.

Two properties are load-bearing and both are asserted here:

* **Additive only (SC-10).**  An agent that belongs to no group must execute the
  *same query it executes today* — not a widened query that happens to select
  the same rows.  ``TestScopePredicateIsAdditive`` compares the generated SQL
  against the shipped constants, byte for byte.
* **Membership is not caller-supplied.**  A recall that widens on a group the
  requester merely *claims* is an authorisation hole.  Membership is read from
  ``hive_group_members`` — the server-side Hive registry — and
  ``TestMembershipComesFromTheServer`` proves the request-scoped
  ``X-Tapps-Group`` header and the process-level ``TAPPS_BRAIN_GROUPS`` env var
  are both ignored.

The discrimination tests carry the controls the defect report demanded: the
out-of-group agent is first shown recalling something it *is* entitled to, so
its miss on the shared row is a real miss and not an empty result for an
unrelated reason; and the same query with the pre-change predicate must miss,
so the test is measuring the fix rather than the fixture.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tapps_brain import _postgres_private_sql as _sql
from tapps_brain._store_query import QueryMixin
from tapps_brain.models import MemoryEntry
from tapps_brain.postgres_connection import PostgresConnectionManager
from tapps_brain.postgres_hive import PostgresHiveBackend
from tapps_brain.postgres_private import PostgresPrivateBackend
from tests._pg_fixture import ensure_rls_role, resolve_fixture_dsn

_GROUP = "tap6695-dev-pipeline"
_GROUP_TAGS = [f"{_sql.GROUP_SCOPE_TAG_PREFIX}{_GROUP}"]
_WRITER = "tap6695-writer"
_SIBLING = "tap6695-in-group-sibling"
_OUTSIDER = "tap6695-out-of-group"
_QUERY = "phosphorescent kingfisher telemetry"
_PROJECT_A = "tap6695-stub-project-a"


_RLS_ROLE = "tap6695_recall_probe"
_RLS_PASSWORD = "tap6695-fixture-only"  # disposable fixture container, never a real credential


@pytest.fixture(scope="module")
def owner_dsn() -> str:
    return resolve_fixture_dsn()


@pytest.fixture(scope="module")
def dsn(owner_dsn: str) -> str:
    """A non-privileged role, so the recall path runs with RLS in force.

    ``PostgresConnectionManager`` refuses to pool a superuser connection
    (``_assert_non_privileged_role``, TAP-512), and a superuser would bypass the
    tenant policy these tests assert on — so the widening must be shown to work
    *under* RLS, not around it.
    """
    return ensure_rls_role(owner_dsn, role=_RLS_ROLE, password=_RLS_PASSWORD, writable=True)


@pytest.fixture(scope="module")
def cm(dsn: str):
    manager = PostgresConnectionManager(dsn)
    yield manager
    manager.close()


@pytest.fixture()
def project_id() -> str:
    return f"tap6695-{uuid.uuid4().hex[:10]}"


def _backend(cm, project_id: str, agent_id: str) -> PostgresPrivateBackend:
    return PostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)


def _entry(key: str, *, tags: list[str]) -> MemoryEntry:
    return MemoryEntry(
        key=key,
        value=f"{_QUERY} — shared operational note",
        tier="pattern",
        tags=tags,
    )


@pytest.fixture()
def shared_row(cm, project_id: str):
    """One row authored by ``_WRITER`` and tagged for ``_GROUP``."""
    key = f"shared-{uuid.uuid4().hex[:8]}"
    _backend(cm, project_id, _WRITER).save(_entry(key, tags=[*_GROUP_TAGS, "critical"]))
    return key


class TestScopePredicateIsAdditive:
    """No groups in, no change out — asserted on the SQL, not on the results."""

    def test_search_sql_is_unchanged_without_group_tags(self) -> None:
        kwargs = {
            "memory_group": None,
            "since": None,
            "until": None,
            "time_field": "created_at",
            "memory_class": None,
            "as_of": None,
        }
        baseline, baseline_params = _sql.build_search_sql(**kwargs)
        for empty in (None, []):
            widened, params = _sql.build_search_sql(**kwargs, group_tags=empty)
            assert widened == baseline
            assert params == baseline_params
        assert "project_id = %s AND agent_id = %s" in baseline
        assert "tags ?|" not in baseline

    def test_knn_sql_is_unchanged_without_group_tags(self) -> None:
        baseline, baseline_params = _sql.build_knn_search_sql()
        for empty in (None, []):
            widened, params = _sql.build_knn_search_sql(group_tags=empty)
            assert widened == baseline
            assert params == baseline_params
        assert "tags ?|" not in baseline

    def test_load_one_sql_is_unchanged_without_group_tags(self) -> None:
        assert _sql.build_load_one_sql(None) == _sql.LOAD_ONE_SQL
        assert _sql.build_load_one_sql([]) == _sql.LOAD_ONE_SQL

    def test_group_tags_widen_the_agent_half_only(self) -> None:
        """Tenant isolation must be untouched — only the agent term may change."""
        widened, _ = _sql.build_search_sql(
            memory_group=None,
            since=None,
            until=None,
            time_field="created_at",
            memory_class=None,
            as_of=None,
            group_tags=_GROUP_TAGS,
        )
        assert "project_id = %s AND (agent_id = %s OR tags ?| %s::text[])" in widened
        # No second project_id term, and none of them is OR'd away.
        assert widened.count("project_id = %s") == 1
        assert "OR project_id" not in widened

    def test_scope_params_track_the_predicate(self) -> None:
        assert _sql.scope_params("p", "a", None) == ["p", "a"]
        assert _sql.scope_params("p", "a", _GROUP_TAGS) == ["p", "a", _GROUP_TAGS]

    def test_group_scope_tags_derives_the_tag_convention(self) -> None:
        assert _sql.group_scope_tags(["a", "b"]) == ["scope:group:a", "scope:group:b"]
        assert _sql.group_scope_tags(None) == []


class TestThreeWayDiscrimination:
    """The executed proof: sibling hits, outsider misses, and the miss is real."""

    def test_writer_recalls_its_own_row(self, cm, project_id, shared_row) -> None:
        """Baseline: the row exists and is findable by the query used throughout."""
        hits = _backend(cm, project_id, _WRITER).search(_QUERY)
        assert [e.key for e in hits] == [shared_row]

    def test_negative_control_sibling_misses_on_the_pre_change_predicate(
        self, cm, project_id, shared_row
    ) -> None:
        """Without group tags — i.e. the SQL that shipped — the sibling must miss.

        If this ever passes, the discrimination test below is measuring
        something other than the fix.
        """
        hits = _backend(cm, project_id, _SIBLING).search(_QUERY)
        assert hits == []

    def test_in_group_sibling_recalls_the_shared_row(self, cm, project_id, shared_row) -> None:
        hits = _backend(cm, project_id, _SIBLING).search(_QUERY, group_tags=_GROUP_TAGS)
        assert [e.key for e in hits] == [shared_row]

    def test_positive_control_outsider_can_recall_its_own_row(
        self, cm, project_id, shared_row
    ) -> None:
        """Prove the outsider's connection works before trusting its empty result."""
        own_key = f"outsider-own-{uuid.uuid4().hex[:8]}"
        outsider = _backend(cm, project_id, _OUTSIDER)
        outsider.save(_entry(own_key, tags=["private"]))
        hits = outsider.search(_QUERY)
        assert own_key in {e.key for e in hits}

    def test_out_of_group_agent_does_not_recall_the_shared_row(
        self, cm, project_id, shared_row
    ) -> None:
        """An agent in a *different* group is not widened into this one."""
        other_tags = _sql.group_scope_tags(["tap6695-unrelated-guild"])
        hits = _backend(cm, project_id, _OUTSIDER).search(_QUERY, group_tags=other_tags)
        assert shared_row not in {e.key for e in hits}

    def test_an_untagged_row_is_never_widened_into(self, cm, project_id) -> None:
        """Widening is by explicit tag: another agent's private row stays private."""
        private_key = f"writer-private-{uuid.uuid4().hex[:8]}"
        _backend(cm, project_id, _WRITER).save(_entry(private_key, tags=["critical"]))
        hits = _backend(cm, project_id, _SIBLING).search(_QUERY, group_tags=_GROUP_TAGS)
        assert private_key not in {e.key for e in hits}

    def test_tenant_isolation_is_unchanged(self, cm, project_id, shared_row) -> None:
        """The same group tag must not reach across projects."""
        other_project = f"tap6695-other-{uuid.uuid4().hex[:8]}"
        hits = _backend(cm, other_project, _SIBLING).search(_QUERY, group_tags=_GROUP_TAGS)
        assert hits == []

    def test_load_one_follows_the_same_rule(self, cm, project_id, shared_row) -> None:
        """The KNN fallback hydrates by key — widen there too or the fix is inert."""
        sibling = _backend(cm, project_id, _SIBLING)
        assert sibling.load_one(shared_row) is None
        widened = sibling.load_one(shared_row, group_tags=_GROUP_TAGS)
        assert widened is not None
        assert widened.key == shared_row


class TestGroupMembershipIsProjectScoped:
    """TAP-6695 differential: ``hive_group_members`` membership must not cross
    projects. Before the fix, ``get_agent_groups(agent_id)`` was keyed on
    ``agent_id`` alone — an agent registered as a group member under one
    project gained group-scoped recall in *every* project it held rows under
    (the production shape: ``default`` was a member of ``nlt-store-fleet``
    and held rows in 177 different projects). This exercises the real
    ``PostgresHiveBackend`` (``add_group_member`` / ``get_agent_groups``)
    against the throwaway fixture container, not a stub.
    """

    _GROUP = "tap6695-tenancy-guild"
    _GROUP_TAGS = [f"{_sql.GROUP_SCOPE_TAG_PREFIX}{_GROUP}"]
    _MEMBER = "tap6695-tenancy-member"

    @pytest.fixture()
    def hive(self, cm: PostgresConnectionManager) -> PostgresHiveBackend:
        return PostgresHiveBackend(cm)

    @pytest.fixture()
    def project_b(self) -> str:
        return f"tap6695-projb-{uuid.uuid4().hex[:10]}"

    @pytest.fixture()
    def membership(self, hive: PostgresHiveBackend, project_id: str) -> None:
        """Register ``_MEMBER`` in ``_GROUP`` for project A ONLY — never project B."""
        hive.create_group(self._GROUP)
        added = hive.add_group_member(self._GROUP, self._MEMBER, project_id)
        assert added is True

    def test_positive_control_member_recalls_the_shared_row_in_project_a(
        self, cm, hive, project_id, membership
    ) -> None:
        """Show FIRST that membership genuinely widens recall in project A —
        an absence proven later (project B) is only meaningful next to this."""
        groups = hive.get_agent_groups(self._MEMBER, project_id)
        assert groups == [self._GROUP]
        stub = _StubStore(hive=hive, project_id=project_id, agent_id=self._MEMBER)
        assert stub._recall_group_tags() == self._GROUP_TAGS

        _backend(cm, project_id, _WRITER).save(
            _entry(f"tenancy-shared-{uuid.uuid4().hex[:8]}", tags=[*self._GROUP_TAGS, "critical"])
        )
        hits = _backend(cm, project_id, self._MEMBER).search(_QUERY, group_tags=self._GROUP_TAGS)
        assert len(hits) == 1

    def test_membership_registered_for_project_a_grants_no_groups_in_project_b(
        self, hive, project_id, project_b, membership
    ) -> None:
        """The exact TAP-6695 defect, on the membership lookup itself: the same
        ``agent_id`` (playing the role production's ``default`` identity
        plays) queries a DIFFERENT project it was never registered under —
        ``get_agent_groups`` must return no groups there."""
        assert hive.get_agent_groups(self._MEMBER, project_b) == []
        stub = _StubStore(hive=hive, project_id=project_b, agent_id=self._MEMBER)
        assert stub._recall_group_tags() is None

    def test_same_agent_does_not_recall_a_project_b_row_shared_to_the_same_group(
        self, cm, hive, project_id, project_b, membership
    ) -> None:
        """End-to-end: a row genuinely shared to ``_GROUP`` in project B exists
        and is tagged correctly — ``_MEMBER`` still cannot recall it, because
        its project-A-only membership derives no group tags in project B."""
        key = f"tenancy-shared-b-{uuid.uuid4().hex[:8]}"
        _backend(cm, project_b, _WRITER).save(_entry(key, tags=[*self._GROUP_TAGS, "critical"]))

        stub = _StubStore(hive=hive, project_id=project_b, agent_id=self._MEMBER)
        group_tags = stub._recall_group_tags()
        assert group_tags is None

        hits = _backend(cm, project_b, self._MEMBER).search(_QUERY, group_tags=group_tags)
        assert key not in {e.key for e in hits}
        assert hits == []


class TestMembershipComesFromTheServer:
    """Where the group list may come from — and where it may not."""

    def test_membership_is_read_from_the_hive_registry(self) -> None:
        class _Hive:
            def __init__(self) -> None:
                self.asked_for: list[tuple[str, str]] = []

            def get_agent_groups(self, agent_id: str, project_id: str) -> list[str]:
                self.asked_for.append((agent_id, project_id))
                return [_GROUP]

        store = _StubStore(hive=_Hive())
        assert store._recall_group_tags() == _GROUP_TAGS
        assert store._hive_store.asked_for == [(_SIBLING, _PROJECT_A)]

    def test_no_hive_backend_means_no_widening(self) -> None:
        assert _StubStore(hive=None)._recall_group_tags() is None

    def test_no_project_id_means_no_widening(self) -> None:
        """TAP-6695: an unresolved project_id must not fall through to a
        membership lookup at all — there is no project to scope it to."""

        class _Hive:
            def get_agent_groups(self, agent_id: str, project_id: str) -> list[str]:
                raise AssertionError("must not be called without a project_id")

        assert _StubStore(hive=_Hive(), project_id=None)._recall_group_tags() is None
        assert _StubStore(hive=_Hive(), project_id="")._recall_group_tags() is None

    def test_agent_in_no_group_gets_no_widening(self) -> None:
        class _Hive:
            def get_agent_groups(self, agent_id: str, project_id: str) -> list[str]:
                return []

        assert _StubStore(hive=_Hive())._recall_group_tags() is None

    def test_a_failing_lookup_degrades_to_own_rows(self) -> None:
        """A registry outage must narrow recall, never widen it."""

        class _Hive:
            def get_agent_groups(self, agent_id: str, project_id: str) -> list[str]:
                raise RuntimeError("registry down")

        assert _StubStore(hive=_Hive())._recall_group_tags() is None

    def test_caller_supplied_group_is_not_a_membership_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``X-Tapps-Group`` and ``TAPPS_BRAIN_GROUPS`` must not widen recall.

        The header is set by the caller and the env var describes the *server
        process*, not the requesting agent.  Either one as a membership source
        would let an agent widen its own recall by asserting a group it does not
        belong to.
        """
        from tapps_brain.mcp_server import context as mcp_context

        monkeypatch.setenv("TAPPS_BRAIN_GROUPS", _GROUP)
        token = mcp_context.REQUEST_GROUP.set(_GROUP)
        try:

            class _Hive:
                def get_agent_groups(self, agent_id: str, project_id: str) -> list[str]:
                    return []

            assert _StubStore(hive=_Hive())._recall_group_tags() is None
        finally:
            mcp_context.REQUEST_GROUP.reset(token)


class _StubStore:
    """Minimal carrier for the two attributes ``_recall_group_tags`` reads.

    Borrows the real method off :class:`~tapps_brain._store_query.QueryMixin`
    rather than reimplementing it, so this cannot pass against a copy that has
    drifted from the code recall actually runs.
    """

    _recall_group_tags = QueryMixin._recall_group_tags

    def __init__(
        self,
        *,
        hive: object,
        project_id: str | None = _PROJECT_A,
        agent_id: str = _SIBLING,
    ) -> None:
        self._hive_store = hive
        self._hive_agent_id = agent_id
        self._project_id = project_id


def _cleanup(dsn: str) -> None:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM private_memories WHERE project_id LIKE 'tap6695-%'")
            cur.execute("DELETE FROM hive_group_members WHERE agent_id LIKE 'tap6695-%'")
            cur.execute("DELETE FROM hive_groups WHERE name LIKE 'tap6695-%'")
        conn.commit()


@pytest.fixture(scope="module", autouse=True)
def _drop_test_rows(owner_dsn: str):
    yield
    _cleanup(owner_dsn)
