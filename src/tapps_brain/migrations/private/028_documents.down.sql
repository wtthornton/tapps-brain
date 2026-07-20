-- Undo migration 028 (documents): drop the document plane tables, trigger
-- functions, and policies.

DROP POLICY IF EXISTS document_chunks_tenant_isolation ON document_chunks;
DROP POLICY IF EXISTS documents_tenant_isolation ON documents;

DROP TRIGGER IF EXISTS trg_document_chunks_search_vector ON document_chunks;
DROP FUNCTION IF EXISTS document_chunks_search_vector_update();

DROP TABLE IF EXISTS document_chunks CASCADE;
DROP TABLE IF EXISTS documents CASCADE;

DELETE FROM private_schema_version WHERE version = 28;
