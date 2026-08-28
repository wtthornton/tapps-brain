"""Refuse to run the test suite against the deployed brain (TAP-6698, VAL-09).

**This module exists because it already happened.** On 2026-08-07 22:58:48-50 UTC
ten rows carrying the tiers ``identity`` / ``long-term`` / ``short-term`` were
written into the deployed ``tapps-brain-db``'s ``private_memories`` under four
``tmp_path``-derived project ids, ``agent_id='default'``,
``source_agent='unknown'``, with keys ``test-identity``, ``lt-1``,
``persist-identity``, ``multi-short-term`` and friends. Those are the literal
keys and tiers in ``tests/integration/test_profile_integration.py`` (see
``TestExplicitProfileCustomTiers`` at :74 and the persistence test at :369):
that file constructs a real ``MemoryStore`` with the built-in
``personal-assistant`` profile, whose layer names *are* those three tiers.

The route in was ``tests/conftest.py``'s autouse in-memory-backend fixture,
which deliberately steps aside when ``TAPPS_BRAIN_DATABASE_URL`` is set
(``_inject_in_memory_private_backend``, condition at
``tests/conftest.py:453``). On a machine where that env var points at the live
brain — which direnv does, via ``.envrc`` → ``.env`` — the whole integration
suite writes to production. Nothing else in the suite noticed, because writing
a profile layer tier is *legal*: ``normalize_save_tier`` resolves it against
the store's in-process profile and ``MemoryEntry._normalize_tier``
(``src/tapps_brain/models.py:422-450``) passes unrecognised strings straight
through as possible EPIC-010 layer names. The profile itself was never
persisted — all 102 rows in live ``project_profiles`` are ``repo-brain`` — so
the tiers arrived with nothing able to price them, which is what crashes
``decay._get_half_life`` and what the SLO-1 inner join used to hide.

``tests/_pg_fixture.py`` re-opened the same door in this lane's own new code:
its docstring promises "Never points at ``tapps-brain-db``" while
``resolve_fixture_dsn`` returns ``TAPPS_BRAIN_DATABASE_URL`` verbatim when set,
and those tests write rows.

The guard is name-based on purpose. The deployed database is
``tapps_brain`` (``docker/docker-compose.hive.yaml:34``); CI's Python matrix
uses ``tapps_brain_dev`` (``.github/workflows/ci.yml:57``) and the local
disposable fixture uses ``tapps_brain_fixture`` (``tests/_pg_fixture.py``), so
neither is affected. It fails loudly rather than silently redirecting to an
in-memory backend: a test that believes it is exercising Postgres must not be
quietly given something else.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

#: Database names that belong to a deployed brain, not to a test fixture.
#: Sourced from ``docker/docker-compose.hive.yaml`` (``POSTGRES_DB: tapps_brain``).
PRODUCTION_DB_NAMES = frozenset({"tapps_brain"})

#: Set to ``1`` to run tests against a production-named database anyway. Exists
#: for a deliberate, supervised exercise against a restored copy — never for
#: getting a red suite green.
ALLOW_ENV_VAR = "TAPPS_BRAIN_ALLOW_LIVE_DSN_IN_TESTS"


def database_name(dsn: str) -> str:
    """Return the database component of *dsn*, or ``""`` if it has none.

    Handles both URL-shaped DSNs (``postgresql://u:p@h:5432/db``) and the
    libpq keyword form (``dbname=tapps_brain host=...``), because psycopg
    accepts both and a guard that only understood one would be trivially
    bypassed by the other.
    """
    text = dsn.strip()
    if not text:
        return ""
    if "://" in text:
        return urlsplit(text).path.lstrip("/").split("?", 1)[0]
    for token in text.split():
        if token.startswith("dbname="):
            return token.split("=", 1)[1]
    return ""


def is_production_dsn(dsn: str) -> bool:
    """Whether *dsn* names a deployed-brain database."""
    return database_name(dsn) in PRODUCTION_DB_NAMES


def live_dsn_refusal(dsn: str, *, source: str) -> str | None:
    """Return a refusal message if *dsn* targets the deployed brain, else ``None``.

    Args:
        dsn: The DSN under consideration.
        source: Where it came from (an env var name, a fixture name) — quoted
            back in the message so the reader knows what to change.
    """
    if not dsn or not is_production_dsn(dsn):
        return None
    if os.environ.get(ALLOW_ENV_VAR, "").strip() in ("1", "true", "TRUE", "yes", "YES"):
        return None
    return (
        f"{source} points at database {database_name(dsn)!r}, which is a deployed "
        f"brain (docker/docker-compose.hive.yaml). The test suite writes rows: "
        f"this is how ten out-of-enum-tier rows reached production on "
        f"2026-08-07 (TAP-6698, VAL-09 — see tests/_live_dsn_guard.py). "
        f"Point {source} at a disposable database (CI uses 'tapps_brain_dev'), "
        f"unset it to let the local fixture container start one, or set "
        f"{ALLOW_ENV_VAR}=1 if you genuinely mean to write to it."
    )
