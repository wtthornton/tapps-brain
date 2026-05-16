-- Undo migration 003: drop feedback_events and session_chunks tables,
-- their indexes (CASCADE), and the search-vector trigger function.

DROP TRIGGER IF EXISTS trg_session_chunks_search_vector ON session_chunks;
DROP FUNCTION IF EXISTS session_chunks_search_vector_update();
DROP TABLE IF EXISTS session_chunks CASCADE;
DROP TABLE IF EXISTS feedback_events CASCADE;

DELETE FROM private_schema_version WHERE version = 3;
