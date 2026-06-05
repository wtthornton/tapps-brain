"""TAP-2867: evidence ``utility_score`` NOT NULL regression.

``kg_evidence.utility_score`` is ``REAL NOT NULL DEFAULT 0.0`` (migration 018),
but :class:`tapps_brain.experience.EvidenceSpec` and
:meth:`tapps_brain.postgres_kg.PostgresKnowledgeGraphStore.attach_evidence`
default ``utility_score`` to ``None``.  Binding an explicit ``NULL`` overrides
the column ``DEFAULT`` and raises ``psycopg.errors.NotNullViolation`` on every
evidence attach without an explicit score — a real 500 that surfaced once
TAP-2866 let a request reach the DB write instead of failing at edge
validation.  ``ATTACH_EVIDENCE_SQL`` now ``COALESCE``-s the bound value to 0.0,
fixing every call site (``record`` / ``record_many`` / ``attach_evidence``).
"""

from __future__ import annotations

from tapps_brain._postgres_kg_sql import ATTACH_EVIDENCE_SQL


def test_attach_evidence_sql_coalesces_null_utility_score() -> None:
    normalized = " ".join(ATTACH_EVIDENCE_SQL.split())
    assert "COALESCE(%s, 0.0)" in normalized


def test_evidence_spec_still_allows_unset_utility_score() -> None:
    """The spec stays nullable; the DB write — not the model — supplies 0.0."""
    from tapps_brain.experience import EvidenceSpec

    spec = EvidenceSpec(edge_id="00000000-0000-0000-0000-000000000000")
    assert spec.utility_score is None
