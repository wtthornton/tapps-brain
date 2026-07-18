"""Migration tooling for PostgreSQL Hive and Federation backends.

EPIC-055 STORY-055.9 — reads SQL migration files and applies them in order,
tracking applied versions in ``hive_schema_version`` / ``federation_schema_version``.
STORY-066.8 — auto-migrate private schema on startup when TAPPS_BRAIN_AUTO_MIGRATE=1.
STORY-074.5 — pg_advisory_lock serialises concurrent migration runners (TAP-1492).
"""

from __future__ import annotations

import hashlib
import importlib.resources
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Migration file discovery
# ---------------------------------------------------------------------------

# Pattern: NNN_description.sql  (e.g., 001_initial.sql)
# Matches forward migrations (e.g. 001_initial.sql) but NOT down files.
_MIGRATION_FILE_RE = re.compile(r"^(\d+)_.+(?<!\.down)\.sql$")

# Matches rollback (down) migration files (e.g. 001_initial.down.sql).
_DOWN_MIGRATION_FILE_RE = re.compile(r"^(\d+)_.+\.down\.sql$")


def _migration_lock_id(version_table: str) -> int:
    """Return a stable positive 63-bit advisory lock ID derived from *version_table*.

    Uses the first 16 hex digits of the MD5 digest, masked to a positive signed
    64-bit integer so it is safe to pass to ``pg_advisory_lock(bigint)``.

    MD5 is used purely as a fast, stable name->int hash (advisory lock id), not
    for any security purpose, so ``usedforsecurity=False`` is set.
    """
    digest = hashlib.md5(version_table.encode(), usedforsecurity=False).hexdigest()
    h = int(digest[:16], 16)
    return h & 0x7FFFFFFFFFFFFFFF  # keep within signed int8 range


def _discover_migration_files(package_path: str) -> list[tuple[int, str, str]]:
    """Discover migration SQL files from a package resource directory.

    Returns a sorted list of ``(version, filename, sql_content)`` tuples.
    """
    results: list[tuple[int, str, str]] = []
    try:
        ref = importlib.resources.files("tapps_brain.migrations").joinpath(package_path)
        for item in ref.iterdir():
            m = _MIGRATION_FILE_RE.match(item.name)
            if m is not None:
                version = int(m.group(1))
                sql = item.read_text(encoding="utf-8")
                results.append((version, item.name, sql))
    except (FileNotFoundError, TypeError):
        # Fall back to filesystem path for development / editable installs.
        pkg_dir = Path(__file__).parent / "migrations" / package_path
        if pkg_dir.is_dir():
            for p in sorted(pkg_dir.iterdir()):
                m = _MIGRATION_FILE_RE.match(p.name)
                if m is not None:
                    version = int(m.group(1))
                    sql = p.read_text(encoding="utf-8")
                    results.append((version, p.name, sql))

    results.sort(key=lambda t: t[0])
    return results


def discover_hive_migrations() -> list[tuple[int, str, str]]:
    """Return hive migration files as ``(version, filename, sql)``."""
    return _discover_migration_files("hive")


def discover_federation_migrations() -> list[tuple[int, str, str]]:
    """Return federation migration files as ``(version, filename, sql)``."""
    return _discover_migration_files("federation")


def discover_private_migrations() -> list[tuple[int, str, str]]:
    """Return private-memory migration files as ``(version, filename, sql)``."""
    return _discover_migration_files("private")


# ---------------------------------------------------------------------------
# Schema status
# ---------------------------------------------------------------------------


@dataclass
class SchemaStatus:
    """Status of a schema version table."""

    current_version: int = 0
    applied_versions: list[int] = field(default_factory=list)
    pending_migrations: list[tuple[int, str]] = field(default_factory=list)


def _get_schema_status(
    dsn: str,
    version_table: str,
    migrations: list[tuple[int, str, str]],
) -> SchemaStatus:
    """Read current version from *version_table* and compute pending migrations."""
    try:
        import psycopg
        from psycopg import sql as pg_sql
    except ImportError:
        raise ImportError(
            "psycopg is required for PostgreSQL migrations.\n"
            "Install with: pip install 'psycopg[binary]'"
        ) from None

    status = SchemaStatus()

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # Check if the version table exists.
        cur.execute(
            "SELECT EXISTS (  SELECT FROM information_schema.tables   WHERE table_name = %s)",
            (version_table,),
        )
        _row = cur.fetchone()
        exists = _row[0] if _row else False

        if exists:
            cur.execute(
                pg_sql.SQL("SELECT version FROM {} ORDER BY version").format(
                    pg_sql.Identifier(version_table)
                )
            )
            status.applied_versions = [row[0] for row in cur.fetchall()]
            if status.applied_versions:
                status.current_version = max(status.applied_versions)

    applied_set = set(status.applied_versions)
    status.pending_migrations = [(v, fname) for v, fname, _ in migrations if v not in applied_set]
    return status


def get_hive_schema_status(dsn: str) -> SchemaStatus:
    """Return the current Hive schema status."""
    migrations = discover_hive_migrations()
    return _get_schema_status(dsn, "hive_schema_version", migrations)


def get_federation_schema_status(dsn: str) -> SchemaStatus:
    """Return the current Federation schema status."""
    migrations = discover_federation_migrations()
    return _get_schema_status(dsn, "federation_schema_version", migrations)


def get_private_schema_status(dsn: str) -> SchemaStatus:
    """Return the current private-memory schema status."""
    migrations = discover_private_migrations()
    return _get_schema_status(dsn, "private_schema_version", migrations)


# ---------------------------------------------------------------------------
# Apply migrations
# ---------------------------------------------------------------------------


def _apply_migrations(
    dsn: str,
    version_table: str,
    migrations: list[tuple[int, str, str]],
    *,
    dry_run: bool = False,
) -> list[int]:
    """Apply pending migrations. Returns list of applied version numbers.

    Dedup is by bare version number: a version already present in the version
    table is skipped.  Bundled files must therefore use unique version
    numbers — when several files share one (e.g. the three ``015_*`` private
    migrations), a DB that recorded the version before all of them were
    bundled will silently skip the rest forever.  A warning is emitted when
    duplicates are detected so the situation is at least visible.
    """
    try:
        import psycopg
        from psycopg import sql as pg_sql
    except ImportError:
        raise ImportError(
            "psycopg is required for PostgreSQL migrations.\n"
            "Install with: pip install 'psycopg[binary]'"
        ) from None

    seen_versions: set[int] = set()
    duplicate_versions: set[int] = set()
    for v, _fname, _sql_text in migrations:
        if v in seen_versions:
            duplicate_versions.add(v)
        seen_versions.add(v)
    if duplicate_versions:
        logger.warning(
            "postgres.migrations.duplicate_versions",
            version_table=version_table,
            versions=sorted(duplicate_versions),
            detail=(
                "multiple bundled migration files share a version number; "
                "dedup against the version table is by version only, so a DB "
                "that already recorded one of them will skip the others"
            ),
        )

    applied: list[int] = []

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # Acquire a session-level advisory lock to serialise concurrent migration
        # runners (e.g. multiple replicas starting simultaneously).  The lock is
        # released automatically when the connection closes.  Skipped in dry-run
        # mode to avoid acquiring real DB locks during planning/preview.
        if not dry_run:
            lock_id = _migration_lock_id(version_table)
            cur.execute("SELECT pg_advisory_lock(%s)", (lock_id,))
            logger.debug(
                "postgres.migrations.advisory_lock_acquired",
                version_table=version_table,
                lock_id=lock_id,
            )

        # Check if version table exists to determine already-applied versions.
        cur.execute(
            "SELECT EXISTS (  SELECT FROM information_schema.tables   WHERE table_name = %s)",
            (version_table,),
        )
        _row2 = cur.fetchone()
        table_exists = _row2[0] if _row2 else False

        applied_set: set[int] = set()
        if table_exists:
            cur.execute(
                pg_sql.SQL("SELECT version FROM {}").format(pg_sql.Identifier(version_table))
            )
            applied_set = {row[0] for row in cur.fetchall()}

        pending = [(v, fname, sql) for v, fname, sql in migrations if v not in applied_set]

        if not pending:
            logger.info("postgres.migrations.up_to_date", version_table=version_table)
            return applied

        for version, fname, sql in pending:
            if dry_run:
                logger.info(
                    "postgres.migrations.would_apply",
                    version=version,
                    filename=fname,
                )
                applied.append(version)
                continue

            logger.info(
                "postgres.migrations.applying",
                version=version,
                filename=fname,
            )
            # Execute the migration SQL.
            # Each migration file is expected to be idempotent (IF NOT EXISTS, etc.)
            # and to insert its own version row.
            conn.execute(sql.encode())
            conn.commit()
            applied.append(version)
            logger.info(
                "postgres.migrations.applied",
                version=version,
                filename=fname,
            )

    return applied


def apply_hive_migrations(dsn: str, *, dry_run: bool = False) -> list[int]:
    """Apply pending Hive schema migrations.

    Returns the list of version numbers that were applied.
    """
    migrations = discover_hive_migrations()
    return _apply_migrations(dsn, "hive_schema_version", migrations, dry_run=dry_run)


def apply_federation_migrations(dsn: str, *, dry_run: bool = False) -> list[int]:
    """Apply pending Federation schema migrations.

    Returns the list of version numbers that were applied.
    """
    migrations = discover_federation_migrations()
    return _apply_migrations(dsn, "federation_schema_version", migrations, dry_run=dry_run)


def apply_private_migrations(dsn: str, *, dry_run: bool = False) -> list[int]:
    """Apply pending private-memory schema migrations.

    Returns the list of version numbers that were applied.
    """
    migrations = discover_private_migrations()
    return _apply_migrations(dsn, "private_schema_version", migrations, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Auto-migrate gate (STORY-066.8)
# ---------------------------------------------------------------------------


class MigrationDowngradeError(RuntimeError):
    """Raised when the live DB schema version exceeds the max bundled migration.

    This means the database was previously migrated by a *newer* binary and
    auto-migrating now would be unsafe (downgrade footgun).  An operator must
    manually resolve the schema version mismatch before restarting.
    """


def maybe_auto_migrate_private(dsn: str) -> None:
    """Apply pending private migrations when ``TAPPS_BRAIN_AUTO_MIGRATE=1``.

    Safe to call unconditionally — is a no-op when the env var is absent or
    set to any value other than ``"1"``.

    Raises:
        MigrationDowngradeError: when the DB's current schema version is
            greater than the highest bundled migration version.  This guards
            against running an old binary against a schema that was already
            advanced by a newer deployment.
        ImportError: when ``psycopg`` is not installed.
    """
    if os.environ.get("TAPPS_BRAIN_AUTO_MIGRATE", "0") != "1":
        return

    migrations = discover_private_migrations()
    if not migrations:
        logger.info("postgres.auto_migrate.no_bundled_migrations")
        return

    max_bundled_version = max(v for v, _, _ in migrations)

    # Read current DB version without applying anything.
    status = get_private_schema_status(dsn)

    if status.current_version > max_bundled_version:
        msg = (
            f"DB private schema version {status.current_version} exceeds the "
            f"max bundled migration version {max_bundled_version}. "
            "Refusing to auto-migrate — the database was advanced by a newer "
            "binary.  Run migrations manually or upgrade the binary."
        )
        logger.error(
            "postgres.auto_migrate.downgrade_refused",
            db_version=status.current_version,
            max_bundled=max_bundled_version,
        )
        raise MigrationDowngradeError(msg)

    applied = apply_private_migrations(dsn)
    if applied:
        logger.info(
            "postgres.auto_migrate.completed",
            applied_versions=applied,
        )
    else:
        logger.info("postgres.auto_migrate.already_up_to_date")


# ---------------------------------------------------------------------------
# Down-migration discovery
# ---------------------------------------------------------------------------


def _discover_down_migration_files(package_path: str) -> list[tuple[int, str, str]]:
    """Discover down-migration SQL files from a package resource directory.

    Returns a sorted list of ``(version, filename, sql_content)`` tuples for
    all ``NNN_*.down.sql`` files found under *package_path*.
    """
    results: list[tuple[int, str, str]] = []
    try:
        ref = importlib.resources.files("tapps_brain.migrations").joinpath(package_path)
        for item in ref.iterdir():
            m = _DOWN_MIGRATION_FILE_RE.match(item.name)
            if m is not None:
                version = int(m.group(1))
                sql = item.read_text(encoding="utf-8")
                results.append((version, item.name, sql))
    except (FileNotFoundError, TypeError):
        # Fall back to filesystem path for development / editable installs.
        pkg_dir = Path(__file__).parent / "migrations" / package_path
        if pkg_dir.is_dir():
            for p in sorted(pkg_dir.iterdir()):
                m = _DOWN_MIGRATION_FILE_RE.match(p.name)
                if m is not None:
                    version = int(m.group(1))
                    sql = p.read_text(encoding="utf-8")
                    results.append((version, p.name, sql))

    results.sort(key=lambda t: t[0])
    return results


def discover_hive_down_migrations() -> list[tuple[int, str, str]]:
    """Return Hive down-migration files as ``(version, filename, sql)``."""
    return _discover_down_migration_files("hive")


def discover_federation_down_migrations() -> list[tuple[int, str, str]]:
    """Return Federation down-migration files as ``(version, filename, sql)``."""
    return _discover_down_migration_files("federation")


def discover_private_down_migrations() -> list[tuple[int, str, str]]:
    """Return private-memory down-migration files as ``(version, filename, sql)``."""
    return _discover_down_migration_files("private")


# ---------------------------------------------------------------------------
# Rollback migrations
# ---------------------------------------------------------------------------


def _rollback_migrations(
    dsn: str,
    version_table: str,
    down_migrations: list[tuple[int, str, str]],
    *,
    target_version: int,
    dry_run: bool = False,
) -> list[int]:
    """Apply down-migrations to roll back to *target_version*.

    Executes the down SQL for every **applied** version > *target_version*
    (per the version table), in descending order (newest first).  Versions
    never recorded in the version table are skipped — previously the runner
    executed and reported rollbacks for versions that were never applied,
    so dry-run previews misled operators.  Each down file is expected to
    include a ``DELETE FROM <version_table> WHERE version = N`` statement;
    after the SQL runs the CLI wrapper need not issue a separate cleanup.

    Returns the list of version numbers that were rolled back.
    """
    try:
        import psycopg
    except ImportError:
        raise ImportError(
            "psycopg is required for PostgreSQL migrations.\n"
            "Install with: pip install 'psycopg[binary]'"
        ) from None

    # Only roll back versions the database actually recorded as applied.
    applied_set: set[int] = set()
    with psycopg.connect(dsn) as _conn, _conn.cursor() as _cur:
        _cur.execute(
            "SELECT EXISTS (  SELECT FROM information_schema.tables   WHERE table_name = %s)",
            (version_table,),
        )
        _row = _cur.fetchone()
        if _row and _row[0]:
            from psycopg import sql as pg_sql

            _cur.execute(
                pg_sql.SQL("SELECT version FROM {}").format(pg_sql.Identifier(version_table))
            )
            applied_set = {row[0] for row in _cur.fetchall()}

    # Descending order: roll back newest migration first.
    to_rollback = sorted(
        [
            (v, fname, sql)
            for v, fname, sql in down_migrations
            if v > target_version and v in applied_set
        ],
        key=lambda t: t[0],
        reverse=True,
    )

    if not to_rollback:
        logger.info(
            "postgres.migrations.rollback.nothing_to_do",
            version_table=version_table,
            target_version=target_version,
        )
        return []

    rolled_back: list[int] = []

    with psycopg.connect(dsn) as conn:
        if not dry_run:
            lock_id = _migration_lock_id(version_table)
            conn.execute("SELECT pg_advisory_lock(%s)", (lock_id,))
            logger.debug(
                "postgres.migrations.advisory_lock_acquired",
                version_table=version_table,
                lock_id=lock_id,
            )

        for version, fname, sql in to_rollback:
            if dry_run:
                logger.info(
                    "postgres.migrations.rollback.would_apply",
                    version=version,
                    filename=fname,
                )
                rolled_back.append(version)
                continue

            logger.info(
                "postgres.migrations.rollback.applying",
                version=version,
                filename=fname,
            )
            conn.execute(sql.encode())
            conn.commit()
            rolled_back.append(version)
            logger.info(
                "postgres.migrations.rollback.applied",
                version=version,
                filename=fname,
            )

    return rolled_back


def rollback_private_migrations(
    dsn: str, *, target_version: int, dry_run: bool = False
) -> list[int]:
    """Roll back private-memory migrations to *target_version*.

    Returns the list of version numbers that were rolled back.
    """
    down_migrations = discover_private_down_migrations()
    return _rollback_migrations(
        dsn,
        "private_schema_version",
        down_migrations,
        target_version=target_version,
        dry_run=dry_run,
    )


def rollback_hive_migrations(dsn: str, *, target_version: int, dry_run: bool = False) -> list[int]:
    """Roll back Hive schema migrations to *target_version*.

    Returns the list of version numbers that were rolled back.
    """
    down_migrations = discover_hive_down_migrations()
    return _rollback_migrations(
        dsn,
        "hive_schema_version",
        down_migrations,
        target_version=target_version,
        dry_run=dry_run,
    )


def rollback_federation_migrations(
    dsn: str, *, target_version: int, dry_run: bool = False
) -> list[int]:
    """Roll back Federation schema migrations to *target_version*.

    Returns the list of version numbers that were rolled back.
    """
    down_migrations = discover_federation_down_migrations()
    return _rollback_migrations(
        dsn,
        "federation_schema_version",
        down_migrations,
        target_version=target_version,
        dry_run=dry_run,
    )
