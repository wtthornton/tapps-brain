-- TAP-6697: widen private_memories.status to carry 'contradicted', and mark the
-- legacy valid_from/valid_until text columns deprecated (read-only).
--
-- Timeline of truth (KB-3.2): one helper -- MemoryStore.close_validity() -- closes
-- a row's validity interval by writing invalid_at + status together.  Before this
-- migration `status` had no member for the contradiction case, so a contradicted
-- row kept status='active' and was only hidden from recall by invalid_at.  That
-- made "is this row live?" answerable from two different columns depending on how
-- it died.  Adding 'contradicted' lets close_validity record *why* in `status`.
--
-- Additive only (SC-10): the CHECK is WIDENED, never narrowed.  Every value that
-- was legal before 033 is still legal after it, so an older binary writing
-- 'active'/'stale'/'superseded'/'archived' against a 033 database keeps working.
-- The constraint must be dropped and re-added because Postgres has no
-- ALTER CONSTRAINT for CHECK expressions; the drop+add runs inside the migration
-- runner's transaction, so no window exists where the column is unconstrained.
--
-- The constraint created by migration 027 was inline (ADD COLUMN ... CHECK), so
-- Postgres auto-named it private_memories_status_check.  IF EXISTS keeps this
-- re-runnable on a database where 033 already landed.

ALTER TABLE private_memories
    DROP CONSTRAINT IF EXISTS private_memories_status_check;

ALTER TABLE private_memories
    ADD CONSTRAINT private_memories_status_check
    CHECK (status IN ('active', 'stale', 'superseded', 'archived', 'contradicted'));

COMMENT ON COLUMN private_memories.status IS
    'Lifecycle status: active | stale | superseded | archived | contradicted. '
    'Written together with invalid_at by MemoryStore.close_validity() (TAP-6697). '
    'A row is live only when status = ''active'' -- see _LIVE_ROW_PREDICATE_SQL.';

-- Ruling 5 (TAP-6697): valid_from / valid_until are DEPRECATED, not dropped.
-- They are human-friendly TEXT aliases (GitHub #29) for the canonical timestamptz
-- columns valid_at / invalid_at.  tapps-brain itself no longer originates writes
-- to them: close_validity, the decay refresh and the demotion sweep all write
-- invalid_at.  The columns stay so existing rows keep their data and older
-- binaries keep round-tripping (additive-only across two surfaces, SC-10).

COMMENT ON COLUMN private_memories.valid_from IS
    'DEPRECATED (TAP-6697), read-only. Superseded by valid_at (timestamptz). '
    'Free TEXT: legacy rows predate the Pydantic validator, so every SQL read '
    'must guard the cast with pg_input_is_valid. No tapps-brain maintenance pass '
    'writes this column.';

COMMENT ON COLUMN private_memories.valid_until IS
    'DEPRECATED (TAP-6697), read-only. Superseded by invalid_at (timestamptz). '
    'Free TEXT: legacy rows predate the Pydantic validator, so every SQL read '
    'must guard the cast with pg_input_is_valid. No tapps-brain maintenance pass '
    'writes this column.';

INSERT INTO private_schema_version (version, description)
VALUES (33, 'Widen private_memories.status CHECK to include contradicted; deprecate valid_from/valid_until (TAP-6697)');
