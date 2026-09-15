"""TAP-7295: the agent-literal placeholder set has one definition site.

``project_resolver.STRICT_REFUSED_AGENT_LITERALS`` is the single source of
truth; ``tenancy_migrate.S3_AGENT_IDS`` and ``http.middleware._ANONYMOUS_AGENT_IDS``
must import it rather than carry their own literal, or the three copies can
silently drift apart (see the module docstrings on all three sites).
"""

from __future__ import annotations

import ast
from pathlib import Path

from tapps_brain.http.middleware import _ANONYMOUS_AGENT_IDS
from tapps_brain.maintenance.tenancy_migrate import S3_AGENT_IDS
from tapps_brain.project_resolver import STRICT_REFUSED_AGENT_LITERALS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TENANCY_MIGRATE_PATH = _REPO_ROOT / "src" / "tapps_brain" / "maintenance" / "tenancy_migrate.py"


def test_s3_agent_ids_is_the_same_object_as_the_shared_constant() -> None:
    """``S3_AGENT_IDS`` must be an alias, not a re-derived literal."""
    assert S3_AGENT_IDS is STRICT_REFUSED_AGENT_LITERALS


def test_anonymous_agent_ids_is_the_same_object_as_the_shared_constant() -> None:
    """``_ANONYMOUS_AGENT_IDS`` must be an alias, not a re-derived literal."""
    assert _ANONYMOUS_AGENT_IDS is STRICT_REFUSED_AGENT_LITERALS


def test_tenancy_migrate_has_no_second_frozenset_literal_for_s3_agent_ids() -> None:
    """Source-level guard: ``S3_AGENT_IDS`` must not be assigned a literal ``frozenset(...)``.

    An ``is``-identity check on the imported name (above) cannot fail if a
    later edit reintroduces ``S3_AGENT_IDS = frozenset({...})`` in
    ``tenancy_migrate.py`` alongside a *different* module-level name imported
    under the same alias — this walks the actual AST assignment to close
    that gap.
    """
    tree = ast.parse(_TENANCY_MIGRATE_PATH.read_text(encoding="utf-8"))
    checked = False
    for node in ast.walk(tree):
        target: ast.expr | None
        value: ast.expr | None
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "S3_AGENT_IDS" not in targets:
                continue
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if not (isinstance(target, ast.Name) and target.id == "S3_AGENT_IDS"):
                continue
            value = node.value
        else:
            continue

        checked = True
        is_literal_frozenset = (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset"
        )
        assert not is_literal_frozenset, (
            "S3_AGENT_IDS has been reassigned a literal frozenset(...) — "
            "it must import STRICT_REFUSED_AGENT_LITERALS from project_resolver instead"
        )

    assert checked, "S3_AGENT_IDS assignment not found in tenancy_migrate.py"
