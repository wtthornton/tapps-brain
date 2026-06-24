-- =====================================================================
-- tapps-brain DB Health Evaluation Suite
-- =====================================================================
-- Target: tapps-brain-db (pgvector/pgvector:pg17), DB "tapps_brain"
-- Purpose: Evaluation routines ONLY — read-only health probes, no writes.
--          Analogous to the agent system-prompt evaluation harness:
--          each check emits a verdict row (PASS / WARN / FAIL) plus the
--          metric that drove it, so the suite is greppable and CI-able.
--
-- Run:    docker exec -i tapps-brain-db sh -c \
--           'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -' < scripts/db_health_eval.sql
--   or:   psql "$TAPPS_BRAIN_DSN" -f scripts/db_health_eval.sql
--
-- Conventions:
--   * Every check SELECTs a row shaped (check_id, severity, metric, verdict).
--   * Thresholds are inline and conservative; tune per deployment.
--   * Nothing here mutates data, takes locks beyond AccessShare, or runs
--     ANALYZE/VACUUM. Safe to run against a live brain.
--
-- Schema facts this suite assumes (verified 2026-06-24):
--   embedding            = vector(384)
--   status domain        = active|stale|superseded|archived (+rejected on KG)
--   tier (private)       = context|pattern|procedural|architectural|identity|
--                          long-term|short-term|ephemeral
--   memory_class         = incident|guidance|decision|convention (nullable)
--   integrity_hash_v     = 1 (legacy) | 2 (current canonical)
--   temporal_sensitivity = high|medium|low (nullable)
--   experience_events    = monthly-partitioned; *_default should stay empty
-- =====================================================================

\pset pager off
\timing off

\echo '=================================================================='
\echo ' tapps-brain DB Health Evaluation'
\echo '=================================================================='

-- ---------------------------------------------------------------------
-- 0. ENVIRONMENT / EXTENSIONS
-- ---------------------------------------------------------------------
\echo ''
\echo '--- 0. Environment & extensions ---'

-- 0.1 Required extensions present (vector, pg_trgm, pgcrypto)
SELECT
  '0.1 extensions'                                              AS check_id,
  CASE WHEN bool_and(present) THEN 'PASS' ELSE 'FAIL' END       AS severity,
  string_agg(name || '=' || COALESCE(installed,'MISSING'), ', ') AS metric,
  CASE WHEN bool_and(present) THEN 'all required extensions installed'
       ELSE 'missing extension(s) — embeddings/trigram/crypto degraded' END AS verdict
FROM (
  SELECT r.name,
         e.extversion                       AS installed,
         (e.extname IS NOT NULL)            AS present
  FROM (VALUES ('vector'),('pg_trgm'),('pgcrypto')) AS r(name)
  LEFT JOIN pg_extension e ON e.extname = r.name
) x;

-- 0.2 Database size + connection pressure
SELECT
  '0.2 db_size/conns'                                          AS check_id,
  CASE WHEN used_pct < 80 THEN 'PASS'
       WHEN used_pct < 95 THEN 'WARN' ELSE 'FAIL' END          AS severity,
  format('size=%s, conns=%s/%s (%s%%)',
         pg_size_pretty(db_size), conns, max_conns, round(used_pct)) AS metric,
  CASE WHEN used_pct < 80 THEN 'connection headroom OK'
       ELSE 'connection pool pressure — investigate leaks/pgbouncer' END AS verdict
FROM (
  SELECT pg_database_size(current_database())                  AS db_size,
         (SELECT count(*) FROM pg_stat_activity)               AS conns,
         current_setting('max_connections')::int               AS max_conns,
         100.0 * (SELECT count(*) FROM pg_stat_activity)
               / current_setting('max_connections')::int       AS used_pct
) s;

-- ---------------------------------------------------------------------
-- 1. MIGRATION / SCHEMA VERSION
-- ---------------------------------------------------------------------
\echo ''
\echo '--- 1. Migration & schema version ---'

-- 1.1 Schema version tables advanced past floor (private/hive/federation)
SELECT
  '1.1 schema_versions'                                        AS check_id,
  'INFO'                                                       AS severity,
  format('private=%s hive=%s federation=%s',
    (SELECT max(version) FROM private_schema_version),
    (SELECT max(version) FROM hive_schema_version),
    (SELECT max(version) FROM federation_schema_version))      AS metric,
  'compare against migrations/ dir HEAD — gap => container running stale image' AS verdict;

-- ---------------------------------------------------------------------
-- 2. CORE MEMORY STORE (private_memories)
-- ---------------------------------------------------------------------
\echo ''
\echo '--- 2. Private memory store ---'

-- 2.1 Lifecycle status distribution.
-- CAVEAT: on this brain the `status` column appears non-transitioning
-- (lifecycle is tracked via superseded_by / invalid_at instead — see 2.8).
-- So a 100% "active" reading is NOT evidence of freshness; pair with 5.x.
SELECT
  '2.1 status_mix'                                             AS check_id,
  'INFO'                                                       AS severity,
  format('active=%s%% (active=%s stale=%s superseded=%s archived=%s)',
         round(active_pct,1), n_active, n_stale, n_super, n_arch) AS metric,
  'interpret with 2.8 — status may be unused; trust invalid_at/superseded_by' AS verdict
FROM (
  SELECT
    count(*) FILTER (WHERE status='active')                    AS n_active,
    count(*) FILTER (WHERE status='stale')                     AS n_stale,
    count(*) FILTER (WHERE status='superseded')                AS n_super,
    count(*) FILTER (WHERE status='archived')                  AS n_arch,
    100.0 * count(*) FILTER (WHERE status='active')
          / NULLIF(count(*),0)                                 AS active_pct
  FROM private_memories
) s;

-- 2.8 Is the `status` column actually used? (all-active + supersede/expiry
-- pointers set => the state machine never flips status.)
-- CONFIRMED BENIGN (2026-06-24 source trace): the `status` column is
-- decorative — production recall enforces lifecycle in the Python retrieval
-- layer, not via status. RecallEngine.recall -> inject_memories ->
-- MemoryRetriever.search -> _filter_candidates_to_pending (retrieval.py:417)
-- calls entry.is_temporally_valid(as_of=None), which defaults to now()
-- (models.py:445) and drops any row with invalid_at <= now() or that is
-- superseded. So a frozen-at-'active' status is expected, not a bug.
SELECT
  '2.8 status_transitions'                                     AS check_id,
  'INFO'                                                       AS severity,
  format('%s non-active rows; %s active rows carry superseded_by or past invalid_at',
         n_nonactive, n_active_but_dead)                       AS metric,
  'benign: status is decorative; lifecycle enforced in MemoryRetriever (retrieval.py:417)' AS verdict
FROM (
  SELECT count(*) FILTER (WHERE status <> 'active')            AS n_nonactive,
         count(*) FILTER (WHERE status='active'
                            AND (superseded_by IS NOT NULL
                              OR (invalid_at IS NOT NULL AND invalid_at < now()))) AS n_active_but_dead
  FROM private_memories
) s;

-- 2.2 Invalid status values (domain drift / direct-write corruption)
SELECT
  '2.2 status_domain'                                          AS check_id,
  CASE WHEN bad = 0 THEN 'PASS' ELSE 'FAIL' END                AS severity,
  format('%s rows with out-of-domain status', bad)             AS metric,
  CASE WHEN bad = 0 THEN 'status values all in domain'
       ELSE 'CHECK constraint bypassed somewhere — audit writers' END AS verdict
FROM (SELECT count(*) bad FROM private_memories
      WHERE status NOT IN ('active','stale','superseded','archived')) s;

-- 2.3 Empty / whitespace-only values (junk memories)
SELECT
  '2.3 empty_values'                                           AS check_id,
  CASE WHEN bad = 0 THEN 'PASS' WHEN bad < 10 THEN 'WARN' ELSE 'FAIL' END AS severity,
  format('%s active memories with blank value', bad)           AS metric,
  CASE WHEN bad = 0 THEN 'no blank payloads'
       ELSE 'blank-value memories pollute recall — investigate writer' END AS verdict
FROM (SELECT count(*) bad FROM private_memories
      WHERE status='active' AND (value IS NULL OR btrim(value) = '')) s;

-- 2.4 Confidence out of [0,1] band
SELECT
  '2.4 confidence_band'                                        AS check_id,
  CASE WHEN bad = 0 THEN 'PASS' ELSE 'FAIL' END                AS severity,
  format('%s rows with confidence outside [0,1]', bad)         AS metric,
  CASE WHEN bad = 0 THEN 'confidence well-formed'
       ELSE 'confidence corruption — ranking math will misbehave' END AS verdict
FROM (SELECT count(*) bad FROM private_memories
      WHERE confidence IS NOT NULL AND (confidence < 0 OR confidence > 1)) s;

-- 2.5 Duplicate logical keys within a project (PK/unique drift)
SELECT
  '2.5 dup_keys'                                               AS check_id,
  CASE WHEN dups = 0 THEN 'PASS' ELSE 'FAIL' END               AS severity,
  format('%s (project_id,agent_id,key) groups with >1 row', dups) AS metric,
  CASE WHEN dups = 0 THEN 'memory keys unique within scope'
       ELSE 'duplicate keys — last-write-wins recall is nondeterministic' END AS verdict
FROM (
  SELECT count(*) dups FROM (
    SELECT 1 FROM private_memories
    GROUP BY project_id, agent_id, key
    HAVING count(*) > 1
  ) g
) s;

-- 2.6 memory_class backfill coverage (migration 015 added it; often NULL)
SELECT
  '2.6 memory_class_fill'                                      AS check_id,
  CASE WHEN filled_pct >= 80 THEN 'PASS'
       WHEN filled_pct >= 20 THEN 'WARN' ELSE 'FAIL' END       AS severity,
  format('%s%% of active memories have memory_class', round(filled_pct,1)) AS metric,
  CASE WHEN filled_pct >= 80 THEN 'classification populated'
       ELSE 'memory_class largely NULL — classifier/backfill never ran' END AS verdict
FROM (
  SELECT 100.0 * count(*) FILTER (WHERE memory_class IS NOT NULL)
              / NULLIF(count(*),0) AS filled_pct
  FROM private_memories WHERE status='active'
) s;

-- 2.7 Invalid memory_class values
SELECT
  '2.7 memory_class_domain'                                    AS check_id,
  CASE WHEN bad = 0 THEN 'PASS' ELSE 'FAIL' END                AS severity,
  format('%s rows with out-of-domain memory_class', bad)       AS metric,
  'domain = incident|guidance|decision|convention'             AS verdict
FROM (SELECT count(*) bad FROM private_memories
      WHERE memory_class IS NOT NULL
        AND memory_class NOT IN ('incident','guidance','decision','convention')) s;

-- ---------------------------------------------------------------------
-- 3. EMBEDDING / SEARCH COVERAGE
-- ---------------------------------------------------------------------
\echo ''
\echo '--- 3. Embedding & search-vector coverage ---'

-- 3.1 Active memories missing an embedding (semantic-search blind spots)
SELECT
  '3.1 embed_coverage'                                         AS check_id,
  CASE WHEN null_pct <= 1 THEN 'PASS'
       WHEN null_pct <= 5 THEN 'WARN' ELSE 'FAIL' END          AS severity,
  format('%s%% active memories have NULL embedding (%s rows)',
         round(null_pct,2), n_null)                            AS metric,
  CASE WHEN null_pct <= 1 THEN 'embedding coverage healthy'
       ELSE 'unembedded memories invisible to vector recall — run backfill' END AS verdict
FROM (
  SELECT count(*) FILTER (WHERE embedding IS NULL)             AS n_null,
         100.0 * count(*) FILTER (WHERE embedding IS NULL)
               / NULLIF(count(*),0)                            AS null_pct
  FROM private_memories WHERE status='active'
) s;

-- 3.2 search_vector (FTS) coverage — keyword recall blind spots
SELECT
  '3.2 fts_coverage'                                           AS check_id,
  CASE WHEN null_pct <= 1 THEN 'PASS'
       WHEN null_pct <= 5 THEN 'WARN' ELSE 'FAIL' END          AS severity,
  format('%s%% active memories have NULL search_vector', round(null_pct,2)) AS metric,
  CASE WHEN null_pct <= 1 THEN 'FTS coverage healthy'
       ELSE 'missing tsvector — keyword recall will miss these' END AS verdict
FROM (
  SELECT 100.0 * count(*) FILTER (WHERE search_vector IS NULL)
              / NULLIF(count(*),0) AS null_pct
  FROM private_memories WHERE status='active'
) s;

-- 3.3 Embedding model consistency (mixed models => incomparable vectors)
SELECT
  '3.3 embed_model_mix'                                        AS check_id,
  CASE WHEN n_models <= 1 THEN 'PASS' ELSE 'WARN' END          AS severity,
  format('%s distinct embedding_model_id values: %s',
         n_models, models)                                     AS metric,
  CASE WHEN n_models <= 1 THEN 'single embedding model'
       ELSE 'mixed embedding models — cosine distances not comparable' END AS verdict
FROM (
  SELECT count(DISTINCT embedding_model_id)                    AS n_models,
         string_agg(DISTINCT COALESCE(embedding_model_id,'<null>'), ', ') AS models
  FROM private_memories WHERE embedding IS NOT NULL
) s;

-- ---------------------------------------------------------------------
-- 4. INTEGRITY HASH (tamper / corruption signal)
-- ---------------------------------------------------------------------
\echo ''
\echo '--- 4. Integrity hashing ---'

-- 4.1 Legacy v1 hashes not yet migrated to v2 canonical encoding
SELECT
  '4.1 hash_version'                                           AS check_id,
  CASE WHEN n_v1 = 0 THEN 'PASS'
       WHEN n_v1 < 500 THEN 'WARN' ELSE 'FAIL' END             AS severity,
  format('%s rows still on integrity_hash_v=1 (current=2)', n_v1) AS metric,
  CASE WHEN n_v1 = 0 THEN 'all hashes on canonical v2'
       ELSE 'legacy v1 hashes — verify_integrity_hash shim still load-bearing' END AS verdict
FROM (SELECT count(*) n_v1 FROM private_memories WHERE integrity_hash_v = 1) s;

-- 4.2 Rows that should carry a hash but do not
SELECT
  '4.2 hash_missing'                                           AS check_id,
  CASE WHEN n_missing = 0 THEN 'PASS'
       WHEN n_missing < 200 THEN 'WARN' ELSE 'FAIL' END        AS severity,
  format('%s v2 rows with NULL integrity_hash', n_missing)     AS metric,
  CASE WHEN n_missing = 0 THEN 'integrity hashes populated'
       ELSE 'unhashed memories cannot be tamper-verified' END  AS verdict
FROM (SELECT count(*) n_missing FROM private_memories
      WHERE integrity_hash_v = 2 AND integrity_hash IS NULL) s;

-- ---------------------------------------------------------------------
-- 5. SUPERSEDE / LIFECYCLE CHAIN INTEGRITY
-- ---------------------------------------------------------------------
\echo ''
\echo '--- 5. Supersede & temporal-validity chains ---'

-- 5.1 Dangling superseded_by pointers (points at a key that does not exist)
SELECT
  '5.1 dangling_supersede'                                     AS check_id,
  CASE WHEN dangling = 0 THEN 'PASS'
       WHEN dangling < 50 THEN 'WARN' ELSE 'FAIL' END          AS severity,
  format('%s of %s superseded rows point at a missing key',
         dangling, total)                                      AS metric,
  CASE WHEN dangling = 0 THEN 'supersede chains intact'
       ELSE 'broken supersede pointers — recall may surface tombstoned data' END AS verdict
FROM (
  SELECT
    count(*) FILTER (WHERE m.superseded_by IS NOT NULL AND t.key IS NULL) AS dangling,
    count(*) FILTER (WHERE m.superseded_by IS NOT NULL)                   AS total
  FROM private_memories m
  LEFT JOIN private_memories t
    ON t.project_id = m.project_id AND t.key = m.superseded_by
) s;

-- 5.2 status='superseded' but no superseded_by set (orphan tombstone)
SELECT
  '5.2 super_no_target'                                        AS check_id,
  CASE WHEN bad = 0 THEN 'PASS' WHEN bad < 50 THEN 'WARN' ELSE 'FAIL' END AS severity,
  format('%s superseded rows with NULL superseded_by', bad)    AS metric,
  'superseded status should always name its successor'         AS verdict
FROM (SELECT count(*) bad FROM private_memories
      WHERE status='superseded' AND superseded_by IS NULL) s;

-- 5.3 Temporal validity inversion (invalid_at before valid_at)
SELECT
  '5.3 temporal_inversion'                                     AS check_id,
  CASE WHEN bad = 0 THEN 'PASS' ELSE 'FAIL' END                AS severity,
  format('%s rows with invalid_at < valid_at', bad)            AS metric,
  'inverted validity window — temporal recall filters break'   AS verdict
FROM (SELECT count(*) bad FROM private_memories
      WHERE valid_at IS NOT NULL AND invalid_at IS NOT NULL
        AND invalid_at < valid_at) s;

-- 5.4 Memories past invalid_at but still status='active'.
-- INFO (confirmed benign — see 2.8): recall DOES exclude these. The Python
-- retrieval layer (MemoryRetriever._filter_candidates_to_pending,
-- retrieval.py:417) drops rows where is_temporally_valid(now()) is false, so
-- expired rows are never served despite status staying 'active'. This count
-- just measures the expiry backlog the status column doesn't reflect.
SELECT
  '5.4 expired_but_active'                                     AS check_id,
  'INFO'                                                       AS severity,
  format('%s active rows past invalid_at (status not flipped; recall still excludes them)', bad) AS metric,
  'benign: recall filters on invalid_at via is_temporally_valid; status is decorative' AS verdict
FROM (SELECT count(*) bad FROM private_memories
      WHERE status='active' AND invalid_at IS NOT NULL AND invalid_at < now()) s;

-- ---------------------------------------------------------------------
-- 6. KNOWLEDGE GRAPH INTEGRITY (kg_entities / kg_edges / kg_evidence)
-- ---------------------------------------------------------------------
\echo ''
\echo '--- 6. Knowledge graph ---'

-- 6.1 Dangling edges (subject/object entity missing)
SELECT
  '6.1 kg_dangling_edges'                                      AS check_id,
  CASE WHEN dangling = 0 THEN 'PASS' ELSE 'FAIL' END           AS severity,
  format('%s edges with missing subject/object entity (of %s)',
         dangling, total)                                      AS metric,
  CASE WHEN dangling = 0 THEN 'all edges reference live entities'
       ELSE 'orphan edges — graph traversal will error/skip' END AS verdict
FROM (
  SELECT
    count(*) FILTER (WHERE s.id IS NULL OR o.id IS NULL)       AS dangling,
    count(*)                                                   AS total
  FROM kg_edges e
  LEFT JOIN kg_entities s ON s.id = e.subject_entity_id
  LEFT JOIN kg_entities o ON o.id = e.object_entity_id
) s;

-- 6.2 Self-loop edges (subject == object) — usually a bug
SELECT
  '6.2 kg_self_loops'                                          AS check_id,
  CASE WHEN n = 0 THEN 'PASS' ELSE 'WARN' END                  AS severity,
  format('%s self-referential edges', n)                       AS metric,
  'subject_entity_id = object_entity_id is rarely intentional'  AS verdict
FROM (SELECT count(*) n FROM kg_edges
      WHERE subject_entity_id = object_entity_id) s;

-- 6.3 Contradicted-but-active edges (logical inconsistency)
SELECT
  '6.3 kg_contradicted_active'                                 AS check_id,
  CASE WHEN n = 0 THEN 'PASS' WHEN n < 20 THEN 'WARN' ELSE 'FAIL' END AS severity,
  format('%s edges flagged contradicted yet status=active', n) AS metric,
  'contradicted edges should be demoted out of active recall'  AS verdict
FROM (SELECT count(*) n FROM kg_edges
      WHERE contradicted IS TRUE AND status='active') s;

-- 6.4 kg_evidence pointing at non-existent edges/entities
SELECT
  '6.4 kg_evidence_orphan'                                     AS check_id,
  'INFO'                                                       AS severity,
  format('evidence rows=%s, edges=%s, entities=%s',
    (SELECT count(*) FROM kg_evidence),
    (SELECT count(*) FROM kg_edges),
    (SELECT count(*) FROM kg_entities))                        AS metric,
  'cross-check evidence FK targets if kg_evidence is load-bearing' AS verdict;

-- 6.5 Duplicate entities (same tenant + normalized canonical name)
SELECT
  '6.5 kg_dup_entities'                                        AS check_id,
  CASE WHEN dups = 0 THEN 'PASS' WHEN dups < 10 THEN 'WARN' ELSE 'FAIL' END AS severity,
  format('%s (tenant,canonical_name_norm) groups with >1 entity', dups) AS metric,
  CASE WHEN dups = 0 THEN 'entity resolution clean'
       ELSE 'duplicate entities — dedup/alias-merge needed' END AS verdict
FROM (
  SELECT count(*) dups FROM (
    SELECT 1 FROM kg_entities
    WHERE canonical_name_norm IS NOT NULL
    GROUP BY tenant_id, canonical_name_norm
    HAVING count(*) > 1
  ) g
) s;

-- ---------------------------------------------------------------------
-- 7. HIVE & FEDERATION
-- ---------------------------------------------------------------------
\echo ''
\echo '--- 7. Hive & federation ---'

-- 7.1 Hive supersede chain dangling
SELECT
  '7.1 hive_dangling_supersede'                                AS check_id,
  CASE WHEN dangling = 0 THEN 'PASS' WHEN dangling < 50 THEN 'WARN' ELSE 'FAIL' END AS severity,
  format('%s hive rows point at a missing superseded_by key', dangling) AS metric,
  'broken hive supersede pointers leak retracted shared knowledge' AS verdict
FROM (
  SELECT count(*) dangling
  FROM hive_memories m
  LEFT JOIN hive_memories t
    ON t.namespace = m.namespace AND t.key = m.superseded_by
  WHERE m.superseded_by IS NOT NULL AND t.key IS NULL
) s;

-- 7.2 Hive embedding coverage
SELECT
  '7.2 hive_embed_coverage'                                    AS check_id,
  CASE WHEN null_pct <= 5 THEN 'PASS'
       WHEN null_pct <= 20 THEN 'WARN' ELSE 'FAIL' END         AS severity,
  format('%s%% hive memories have NULL embedding', round(null_pct,1)) AS metric,
  'unembedded hive memories miss cross-agent semantic recall'   AS verdict
FROM (
  SELECT 100.0 * count(*) FILTER (WHERE embedding IS NULL)
              / NULLIF(count(*),0) AS null_pct
  FROM hive_memories
) s;

-- 7.3 Federation subscription liveness (info — pair with subscriptions table)
SELECT
  '7.3 federation_state'                                       AS check_id,
  'INFO'                                                       AS severity,
  format('federated_memories=%s, subscriptions=%s',
    (SELECT count(*) FROM federated_memories),
    (SELECT count(*) FROM federation_subscriptions))           AS metric,
  'zero federated memories with active subscriptions => sync stalled' AS verdict;

-- ---------------------------------------------------------------------
-- 8. PARTITIONING & TIME-SERIES (experience_events)
-- ---------------------------------------------------------------------
\echo ''
\echo '--- 8. Partitioning & event streams ---'

-- 8.1 Default partition must stay empty (rows here = missing month partition)
SELECT
  '8.1 ee_default_partition'                                   AS check_id,
  CASE WHEN n = 0 THEN 'PASS' ELSE 'FAIL' END                  AS severity,
  format('%s rows landed in experience_events_default', n)     AS metric,
  CASE WHEN n = 0 THEN 'pg_partman keeping partitions ahead of writes'
       ELSE 'rows fell to default partition — partition maintenance broke' END AS verdict
FROM (SELECT count(*) n FROM experience_events_default) s;

-- 8.2 Future partition runway (need partitions covering upcoming months)
SELECT
  '8.2 ee_future_runway'                                       AS check_id,
  CASE WHEN months_ahead >= 2 THEN 'PASS'
       WHEN months_ahead >= 1 THEN 'WARN' ELSE 'FAIL' END      AS severity,
  format('%s monthly partitions cover the future', months_ahead) AS metric,
  CASE WHEN months_ahead >= 2 THEN 'partition runway healthy'
       ELSE 'partman premake exhausted — writes will hit default soon' END AS verdict
FROM (
  SELECT count(*) months_ahead
  FROM pg_inherits i
  JOIN pg_class c ON c.oid = i.inhrelid
  WHERE i.inhparent = 'experience_events'::regclass
    AND c.relname ~ '_y[0-9]{4}m[0-9]{2}$'
    AND c.relname > 'experience_events_y' || to_char(now(), 'YYYY"m"MM')
) s;

-- 8.3 Event ingestion freshness (recent events => producers alive)
SELECT
  '8.3 ee_freshness'                                           AS check_id,
  CASE WHEN last_event > now() - interval '24 hours' THEN 'PASS'
       WHEN last_event > now() - interval '7 days'   THEN 'WARN'
       ELSE 'FAIL' END                                        AS severity,
  format('newest experience_event: %s', COALESCE(last_event::text,'<none>')) AS metric,
  CASE WHEN last_event > now() - interval '24 hours' THEN 'event stream live'
       ELSE 'no recent events — flywheel/instrumentation may be down' END AS verdict
FROM (SELECT max(event_time) last_event FROM experience_events) s;

-- ---------------------------------------------------------------------
-- 9. FEEDBACK / FLYWHEEL SIGNAL
-- ---------------------------------------------------------------------
\echo ''
\echo '--- 9. Feedback & flywheel ---'

-- 9.1 Feedback recency (the learning loop should be receiving signal)
SELECT
  '9.1 feedback_freshness'                                     AS check_id,
  CASE WHEN last_fb > now() - interval '7 days' THEN 'PASS'
       WHEN last_fb > now() - interval '30 days' THEN 'WARN' ELSE 'FAIL' END AS severity,
  format('newest feedback_event: %s', COALESCE(last_fb::text,'<none>')) AS metric,
  CASE WHEN last_fb > now() - interval '7 days' THEN 'flywheel receiving feedback'
       ELSE 'no recent feedback — flywheel starved' END        AS verdict
FROM (SELECT max(timestamp) last_fb FROM feedback_events) s;

-- 9.2 Feedback↔memory linkage (INFO).
-- CAVEAT: feedback_events.entry_key is loosely populated on this brain
-- (many NULL; some are test probes like 'tap-1632-probe-entry'), and may
-- target hive/KG rather than private_memories. Treat a high orphan ratio
-- as a linkage-namespace signal, not proof feedback is wasted. Verify the
-- entry_key contract before escalating.
SELECT
  '9.2 feedback_linkage'                                       AS check_id,
  'INFO'                                                       AS severity,
  format('%s%% of recent non-null entry_key feedback has no matching private_memory (n=%s, null_key=%s)',
         round(orphan_pct,1), n_keyed, n_null)                 AS metric,
  'investigate entry_key namespace if this is high AND feedback should hit private_memories' AS verdict
FROM (
  SELECT
    100.0 * count(*) FILTER (WHERE m.key IS NULL AND f.entry_key IS NOT NULL)
          / NULLIF(count(*) FILTER (WHERE f.entry_key IS NOT NULL),0)    AS orphan_pct,
    count(*) FILTER (WHERE f.entry_key IS NOT NULL)                      AS n_keyed,
    count(*) FILTER (WHERE f.entry_key IS NULL)                          AS n_null
  FROM feedback_events f
  LEFT JOIN private_memories m
    ON m.project_id = f.project_id AND m.key = f.entry_key
  WHERE f.timestamp > now() - interval '30 days'
) s;

-- ---------------------------------------------------------------------
-- 10. FSRS / DECAY HEALTH (spaced-repetition fields)
-- ---------------------------------------------------------------------
\echo ''
\echo '--- 10. FSRS / reinforcement decay ---'

-- 10.1 Degenerate FSRS state — TRULY invalid params only.
-- NOTE: stability=0 / difficulty=0 is the *uninitialized default* on this
-- brain (never-reinforced rows), NOT corruption — see 10.3. Only flag
-- negative stability or difficulty outside [0,10] as real damage.
SELECT
  '10.1 fsrs_degenerate'                                       AS check_id,
  CASE WHEN bad = 0 THEN 'PASS' WHEN bad < 100 THEN 'WARN' ELSE 'FAIL' END AS severity,
  format('%s active rows with stability<0 or difficulty out of [0,10]', bad) AS metric,
  'negative/out-of-range FSRS params => retrievability math breaks' AS verdict
FROM (
  SELECT count(*) bad FROM private_memories
  WHERE status='active'
    AND ( stability < 0
       OR (difficulty IS NOT NULL AND (difficulty < 0 OR difficulty > 10)) )
) s;

-- 10.3 FSRS initialization coverage (stability=0 => never scheduled by FSRS)
SELECT
  '10.3 fsrs_uninitialized'                                    AS check_id,
  'INFO'                                                       AS severity,
  format('%s%% of active memories have uninitialized FSRS (stability=0)',
         round(pct,1))                                         AS metric,
  'high share is expected on a write-heavy brain; only a concern if recall ranks on retrievability' AS verdict
FROM (
  SELECT 100.0 * count(*) FILTER (WHERE COALESCE(stability,0) = 0)
              / NULLIF(count(*),0) AS pct
  FROM private_memories WHERE status='active'
) s;

-- 10.2 Reinforcement skew (never-reinforced share of active memories)
SELECT
  '10.2 never_reinforced'                                      AS check_id,
  'INFO'                                                       AS severity,
  format('%s%% of active memories never reinforced (count=%s)',
         round(pct,1), n)                                      AS metric,
  'high never-reinforced share is normal for a write-heavy brain; track trend' AS verdict
FROM (
  SELECT count(*) FILTER (WHERE COALESCE(reinforce_count,0)=0)                 AS n,
         100.0 * count(*) FILTER (WHERE COALESCE(reinforce_count,0)=0)
               / NULLIF(count(*),0)                                           AS pct
  FROM private_memories WHERE status='active'
) s;

-- ---------------------------------------------------------------------
-- 11. STORAGE / BLOAT / INDEX HEALTH
-- ---------------------------------------------------------------------
\echo ''
\echo '--- 11. Storage, bloat & autovacuum ---'

-- 11.1 Dead-tuple bloat on hot tables (autovacuum keeping up?)
SELECT
  '11.1 dead_tuple_bloat'                                      AS check_id,
  CASE WHEN max_dead_pct < 20 THEN 'PASS'
       WHEN max_dead_pct < 40 THEN 'WARN' ELSE 'FAIL' END      AS severity,
  format('worst dead-tuple ratio: %s = %s%%', worst_tbl, round(max_dead_pct,1)) AS metric,
  CASE WHEN max_dead_pct < 20 THEN 'autovacuum keeping up'
       ELSE 'bloat building — check autovacuum settings / long txns' END AS verdict
FROM (
  SELECT relname AS worst_tbl,
         100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0) AS max_dead_pct
  FROM pg_stat_user_tables
  WHERE n_live_tup + n_dead_tup > 1000
  ORDER BY 100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0) DESC NULLS LAST
  LIMIT 1
) s;

-- 11.2 Tables never (auto)vacuumed despite churn
SELECT
  '11.2 unvacuumed_tables'                                     AS check_id,
  CASE WHEN n = 0 THEN 'PASS' ELSE 'WARN' END                  AS severity,
  format('%s churned tables never vacuumed/autovacuumed', n)   AS metric,
  'first-vacuum debt — visibility map cold, scans slow'        AS verdict
FROM (
  SELECT count(*) n FROM pg_stat_user_tables
  WHERE last_vacuum IS NULL AND last_autovacuum IS NULL
    AND n_dead_tup > 1000
) s;

-- 11.3 Stale statistics (planner working off old ANALYZE)
SELECT
  '11.3 stale_stats'                                           AS check_id,
  CASE WHEN n = 0 THEN 'PASS' ELSE 'WARN' END                  AS severity,
  format('%s busy tables not analyzed in 7+ days', n)          AS metric,
  'stale stats => bad plans on vector/FTS queries'             AS verdict
FROM (
  SELECT count(*) n FROM pg_stat_user_tables
  WHERE n_live_tup > 5000
    AND COALESCE(GREATEST(last_analyze, last_autoanalyze),
                 'epoch'::timestamptz) < now() - interval '7 days'
) s;

-- 11.4 Unused indexes (write amplification with zero read benefit)
SELECT
  '11.4 unused_indexes'                                        AS check_id,
  CASE WHEN n = 0 THEN 'PASS' ELSE 'INFO' END                  AS severity,
  format('%s non-unique indexes >1MB with 0 scans', n)         AS metric,
  'review for drop — they cost INSERT/UPDATE time for no reads' AS verdict
FROM (
  SELECT count(*) n
  FROM pg_stat_user_indexes ui
  JOIN pg_index ix ON ix.indexrelid = ui.indexrelid
  WHERE ui.idx_scan = 0
    AND NOT ix.indisunique
    AND pg_relation_size(ui.indexrelid) > 1024*1024
) s;

-- 11.5 Invalid / unready indexes (failed CONCURRENTLY builds)
SELECT
  '11.5 invalid_indexes'                                       AS check_id,
  CASE WHEN n = 0 THEN 'PASS' ELSE 'FAIL' END                  AS severity,
  format('%s invalid/not-ready indexes', n)                    AS metric,
  CASE WHEN n = 0 THEN 'all indexes valid'
       ELSE 'invalid index — likely a broken CREATE INDEX CONCURRENTLY' END AS verdict
FROM (
  SELECT count(*) n FROM pg_index
  WHERE NOT indisvalid OR NOT indisready
) s;

-- ---------------------------------------------------------------------
-- 12. REPLICATION / TXN-AGE SAFETY
-- ---------------------------------------------------------------------
\echo ''
\echo '--- 12. Transaction-age & long-running queries ---'

-- 12.1 Wraparound headroom (oldest unfrozen xid age vs autovacuum_freeze_max)
SELECT
  '12.1 xid_wraparound'                                        AS check_id,
  CASE WHEN pct < 50 THEN 'PASS' WHEN pct < 80 THEN 'WARN' ELSE 'FAIL' END AS severity,
  format('oldest datfrozenxid age = %s%% of freeze ceiling', round(pct,1)) AS metric,
  CASE WHEN pct < 50 THEN 'wraparound risk low'
       ELSE 'approaching wraparound — ensure autovacuum can freeze' END AS verdict
FROM (
  SELECT 100.0 * max(age(datfrozenxid))
              / current_setting('autovacuum_freeze_max_age')::bigint AS pct
  FROM pg_database
) s;

-- 12.2 Long-running / idle-in-transaction sessions (lock & bloat source)
SELECT
  '12.2 long_txn'                                              AS check_id,
  CASE WHEN n = 0 THEN 'PASS' WHEN n < 3 THEN 'WARN' ELSE 'FAIL' END AS severity,
  format('%s sessions active/idle-in-txn for >5 min', n)       AS metric,
  CASE WHEN n = 0 THEN 'no stuck transactions'
       ELSE 'long txns block vacuum & hold locks — investigate' END AS verdict
FROM (
  SELECT count(*) n FROM pg_stat_activity
  WHERE state IN ('active','idle in transaction')
    AND xact_start < now() - interval '5 minutes'
    AND pid <> pg_backend_pid()
) s;

-- ---------------------------------------------------------------------
-- 13. TENANCY / SCOPE HYGIENE
-- ---------------------------------------------------------------------
\echo ''
\echo '--- 13. Tenancy & scope hygiene ---'

-- 13.1 Memories with NULL/blank project_id (escape row-level scoping)
SELECT
  '13.1 null_project_scope'                                    AS check_id,
  CASE WHEN n = 0 THEN 'PASS' ELSE 'FAIL' END                  AS severity,
  format('%s private_memories with NULL/blank project_id', n)  AS metric,
  CASE WHEN n = 0 THEN 'all memories project-scoped'
       ELSE 'unscoped rows leak across project RLS boundary' END AS verdict
FROM (SELECT count(*) n FROM private_memories
      WHERE project_id IS NULL OR btrim(project_id) = '') s;

-- 13.2 Project profiles without an approved + token state (orphan tenants)
SELECT
  '13.2 profile_token_state'                                   AS check_id,
  'INFO'                                                       AS severity,
  format('%s profiles, %s approved, %s with hashed_token',
    (SELECT count(*) FROM project_profiles),
    (SELECT count(*) FROM project_profiles WHERE approved IS TRUE),
    (SELECT count(*) FROM project_profiles WHERE hashed_token IS NOT NULL)) AS metric,
  'approved profiles lacking a token cannot authenticate writes' AS verdict;

-- ---------------------------------------------------------------------
-- 14. SCORECARD SUMMARY
-- ---------------------------------------------------------------------
\echo ''
\echo '--- 14. One-line scorecard (rough composite) ---'

SELECT
  'SCORECARD'                                                  AS check_id,
  format('pm=%s active_pct=%s embed_null=%s hash_v1=%s dangling_super=%s kg_e=%s kg_v=%s hive=%s ee_default=%s',
    (SELECT count(*) FROM private_memories),
    (SELECT round(100.0*count(*) FILTER (WHERE status='active')/NULLIF(count(*),0)) FROM private_memories),
    (SELECT count(*) FROM private_memories WHERE status='active' AND embedding IS NULL),
    (SELECT count(*) FROM private_memories WHERE integrity_hash_v=1),
    (SELECT count(*) FROM private_memories m LEFT JOIN private_memories t
       ON t.project_id=m.project_id AND t.key=m.superseded_by
       WHERE m.superseded_by IS NOT NULL AND t.key IS NULL),
    (SELECT count(*) FROM kg_entities),
    (SELECT count(*) FROM kg_edges),
    (SELECT count(*) FROM hive_memories),
    (SELECT count(*) FROM experience_events_default)
  )                                                            AS metric;

\echo ''
\echo '=================================================================='
\echo ' End of health evaluation. Grep output for FAIL / WARN.'
\echo '=================================================================='
