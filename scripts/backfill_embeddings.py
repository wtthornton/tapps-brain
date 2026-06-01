#!/usr/bin/env python3
"""One-shot, idempotent backfill of NULL embedding vectors (TAP-2672).

Re-embeds every ``private_memories`` and ``hive_memories`` row whose
``embedding`` column is NULL, in batches, using the same SentenceTransformer
provider the brain uses at write time.  Safe to re-run: it only selects NULL
rows, so a second run after a clean pass updates nothing.

Usage::

    python scripts/backfill_embeddings.py [DSN] [--batch-size N] \\
        [--table private_memories|hive_memories] [--dry-run]

``DSN`` defaults to ``$TAPPS_BRAIN_DATABASE_URL``.  The DSN's role must hold
UPDATE on the target table(s) — the deployment ``tapps_runtime`` role does.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from tapps_brain.embeddings import SentenceTransformerProvider
    from tapps_brain.postgres_connection import PostgresConnectionManager

logger = structlog.get_logger("backfill_embeddings")

_DEFAULT_BATCH = 500


@dataclass(frozen=True)
class _TablePlan:
    """How to read NULL-embedding rows and write them back for one table."""

    table: str
    key_columns: tuple[str, ...]
    has_model_id: bool

    # Table/column names below come only from the hardcoded ``_PLANS`` allowlist
    # (never user input), so the f-string interpolation is not an injection path.
    @property
    def select_sql(self) -> str:
        cols = ", ".join((*self.key_columns, "value"))
        return f"SELECT {cols} FROM {self.table} WHERE embedding IS NULL LIMIT %s"  # nosec B608

    @property
    def count_sql(self) -> str:
        return f"SELECT count(*) FROM {self.table} WHERE embedding IS NULL"  # nosec B608

    def update_sql(self) -> str:
        where = " AND ".join(f"{c} = %s" for c in self.key_columns)
        set_clause = "embedding = %s::vector"
        if self.has_model_id:
            set_clause += ", embedding_model_id = %s"
        return f"UPDATE {self.table} SET {set_clause} WHERE {where}"  # nosec B608


_PLANS: dict[str, _TablePlan] = {
    "private_memories": _TablePlan(
        table="private_memories",
        key_columns=("project_id", "agent_id", "key"),
        has_model_id=True,
    ),
    "hive_memories": _TablePlan(
        table="hive_memories",
        key_columns=("namespace", "key"),
        has_model_id=False,
    ),
}


def backfill_table(
    cm: PostgresConnectionManager,
    provider: SentenceTransformerProvider,
    plan: _TablePlan,
    *,
    batch_size: int = _DEFAULT_BATCH,
    dry_run: bool = False,
) -> int:
    """Embed every NULL-embedding row in *plan.table*; return rows updated.

    Idempotent: each batch re-selects ``WHERE embedding IS NULL``, so already
    backfilled rows are never revisited and a re-run after completion is a
    no-op.  Commits per batch so partial progress survives an interruption.
    """
    model_id = getattr(provider, "model_id", None)
    update_sql = plan.update_sql()
    total = 0
    while True:
        with cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(plan.select_sql, (batch_size,))
            rows = cur.fetchall()
            if not rows:
                break
            values = [str(r[-1] or "") for r in rows]
            vectors = provider.embed_batch(values)
            if dry_run:
                logger.info("backfill.dry_run_batch", table=plan.table, rows=len(rows))
                total += len(rows)
                break
            for row, vec in zip(rows, vectors, strict=True):
                key_vals = row[:-1]
                literal = "[" + ",".join(str(float(v)) for v in vec) + "]"
                if plan.has_model_id:
                    params: tuple[object, ...] = (literal, model_id, *key_vals)
                else:
                    params = (literal, *key_vals)
                cur.execute(update_sql, params)
            conn.commit()
        total += len(rows)
        logger.info("backfill.batch_committed", table=plan.table, batch=len(rows), total=total)
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill NULL embedding vectors (TAP-2672).")
    parser.add_argument("dsn", nargs="?", default=os.environ.get("TAPPS_BRAIN_DATABASE_URL"))
    parser.add_argument("--batch-size", type=int, default=_DEFAULT_BATCH)
    parser.add_argument("--table", choices=sorted(_PLANS), action="append")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.dsn:
        print("ERROR: no DSN (pass as arg or set TAPPS_BRAIN_DATABASE_URL).", file=sys.stderr)
        return 2

    from tapps_brain.embeddings import get_embedding_provider
    from tapps_brain.postgres_connection import PostgresConnectionManager

    provider = get_embedding_provider()
    if provider is None:
        print(
            "ERROR: no embedding provider available (install tapps-brain[all], "
            "set HF_TOKEN / warm the model cache).",
            file=sys.stderr,
        )
        return 1

    cm = PostgresConnectionManager(args.dsn)
    plans = [_PLANS[t] for t in (args.table or sorted(_PLANS))]
    try:
        for plan in plans:
            with cm.get_connection() as conn, conn.cursor() as cur:
                cur.execute(plan.count_sql)
                pending = cur.fetchone()[0]
            logger.info("backfill.start", table=plan.table, pending=pending, dry_run=args.dry_run)
            updated = backfill_table(
                cm, provider, plan, batch_size=args.batch_size, dry_run=args.dry_run
            )
            logger.info("backfill.done", table=plan.table, updated=updated)
    finally:
        cm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
