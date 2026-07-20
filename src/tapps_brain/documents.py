"""Document plane — durable documents beside vector RAG (TAP-4998).

Stores knowledge documents (reports, transcripts, research dumps) with
retained original bytes in the Postgres ``documents`` table and
deterministic chunks for hybrid retrieval in ``document_chunks``
(migration 028).  Tenancy matches ``private_memories``: rows carry
``(project_id, agent_id)`` and are RLS-isolated by ``app.project_id``;
reads are project-scoped so agents on one project share documents.

Explicitly **not** a TTL byte cache (AgentForge ADR-040 owns that) —
``expires_at`` is retention policy swept by :meth:`MemoryStore.gc`.

Design: ``docs/planning/DESIGN-DOCUMENT-STORE.md``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from tapps_brain.postgres_connection import PostgresConnectionManager

logger = structlog.get_logger(__name__)

# Default chunk size (characters).  Deterministic packing — no LLM.
DEFAULT_CHUNK_CHARS = 1000

# Retention grammar: "project" (keep until deleted) or "days:<n>".
_RETENTION_DAYS_RE = re.compile(r"^days:(\d{1,4})$")
_MAX_RETENTION_DAYS = 3650

# Composite RRF key separator — NUL cannot appear in doc_id (uuid hex).
_KEY_SEP = "\x00"

_VALID_INDEX_STATUSES = frozenset({"none", "pending", "indexed", "error"})


class DocumentMeta(BaseModel):
    """Metadata for one stored document (content bytes excluded)."""

    doc_id: str
    title: str
    content_type: str = "text/plain"
    size_bytes: int = Field(ge=0)
    sha256: str
    tags: list[str] = Field(default_factory=list)
    index_status: str = "none"
    index_error: str | None = None
    retention: str = "project"
    agent_id: str = ""
    created_at: str = ""
    expires_at: str | None = None
    chunk_count: int = Field(default=0, ge=0)


class DocumentSearchHit(BaseModel):
    """One RRF-fused hybrid search result over document chunks."""

    doc_id: str
    chunk_no: int = Field(ge=0)
    title: str = ""
    snippet: str = ""
    score: float = 0.0


def parse_retention(retention: str) -> datetime | None:
    """Resolve a retention spec to an ``expires_at`` timestamp.

    ``"project"`` means keep until deleted (returns ``None``);
    ``"days:<n>"`` expires *n* days from now (1 <= n <= 3650).

    Raises:
        ValueError: for any other spec.
    """
    spec = (retention or "").strip()
    if spec == "project":
        return None
    m = _RETENTION_DAYS_RE.match(spec)
    if m is not None:
        days = int(m.group(1))
        if 1 <= days <= _MAX_RETENTION_DAYS:
            return datetime.now(tz=UTC) + timedelta(days=days)
    msg = f"invalid retention {retention!r}: expected 'project' or 'days:<1-{_MAX_RETENTION_DAYS}>'"
    raise ValueError(msg)


def chunk_text(
    text: str,
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    max_chunks: int = 256,
) -> list[str]:
    """Deterministically split *text* into retrieval chunks (no LLM).

    Paragraphs (blank-line separated) are greedily packed into chunks of at
    most *chunk_chars* characters.  A single paragraph longer than
    *chunk_chars* is hard-split, preferring the last whitespace in the
    window so words stay intact.  Output is capped at *max_chunks* — text
    beyond the cap is not chunked (bounds embedding cost per design).
    """
    if chunk_chars < 1 or max_chunks < 1:
        return []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", normalized) if p.strip()]

    pieces: list[str] = []
    for para in paragraphs:
        if len(para) <= chunk_chars:
            pieces.append(para)
            continue
        start = 0
        while start < len(para):
            window = para[start : start + chunk_chars]
            if start + chunk_chars < len(para):
                # Prefer a whitespace break in the back half of the window.
                cut = window.rfind(" ")
                if cut > chunk_chars // 2:
                    window = window[:cut]
            pieces.append(window.strip())
            start += len(window)
            while start < len(para) and para[start] == " ":
                start += 1

    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        if not buf:
            buf = piece
        elif len(buf) + 2 + len(piece) <= chunk_chars:
            buf = f"{buf}\n\n{piece}"
        else:
            chunks.append(buf)
            buf = piece
        if len(chunks) >= max_chunks:
            return chunks
    if buf and len(chunks) < max_chunks:
        chunks.append(buf)
    return chunks


def make_chunk_key(doc_id: str, chunk_no: int) -> str:
    """Composite key for RRF fusion over ``(doc_id, chunk_no)`` pairs."""
    return f"{doc_id}{_KEY_SEP}{chunk_no}"


def split_chunk_key(key: str) -> tuple[str, int]:
    """Invert :func:`make_chunk_key`."""
    doc_id, _, chunk_no = key.rpartition(_KEY_SEP)
    return doc_id, int(chunk_no)


class PostgresDocumentStore:
    """Postgres-backed document store (``documents`` + ``document_chunks``).

    All statements run through
    :meth:`PostgresConnectionManager.project_context` so the fail-closed
    RLS policies from migration 028 scope every read and write to
    *project_id*.  ``agent_id`` records the writer only — reads are
    project-scoped by design (knowledge sharing across agents).
    """

    def __init__(
        self,
        connection_manager: PostgresConnectionManager,
        *,
        project_id: str,
        agent_id: str,
    ) -> None:
        self._cm = connection_manager
        self._project_id = project_id
        self._agent_id = agent_id

    def _scoped_conn(self) -> Any:  # noqa: ANN401
        """Connection context with ``app.project_id`` set for RLS (like
        :meth:`PostgresPrivateBackend._scoped_conn`)."""
        pc = getattr(self._cm, "project_context", None)
        if pc is not None:
            return pc(self._project_id)
        return self._cm.get_connection()

    # ------------------------------------------------------------------
    # CRUD — documents table
    # ------------------------------------------------------------------

    def put(
        self,
        *,
        doc_id: str,
        title: str,
        content: bytes,
        content_type: str,
        sha256: str,
        tags: list[str],
        index_status: str,
        index_error: str | None,
        retention: str,
        expires_at: datetime | None,
    ) -> None:
        """Insert one document row (content bytes retained)."""
        if index_status not in _VALID_INDEX_STATUSES:
            msg = f"invalid index_status {index_status!r}"
            raise ValueError(msg)
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents
                    (project_id, agent_id, doc_id, title, content_type, content,
                     size_bytes, sha256, tags, index_status, index_error,
                     retention, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    self._project_id,
                    self._agent_id,
                    doc_id,
                    title,
                    content_type,
                    content,
                    len(content),
                    sha256,
                    tags,
                    index_status,
                    index_error,
                    retention,
                    expires_at,
                ),
            )
        logger.debug(
            "documents.put",
            project_id=self._project_id,
            doc_id=doc_id,
            size_bytes=len(content),
            index_status=index_status,
        )

    def save_chunks(
        self,
        doc_id: str,
        chunks: list[str],
        embeddings: list[list[float] | None],
    ) -> int:
        """Insert chunk rows for *doc_id*; returns the number stored.

        *embeddings* must align 1:1 with *chunks*; ``None`` entries store a
        NULL embedding (lexical-only chunk).
        """
        if len(chunks) != len(embeddings):
            msg = "chunks and embeddings must have equal length"
            raise ValueError(msg)
        if not chunks:
            return 0
        params_seq = [
            (
                self._project_id,
                doc_id,
                chunk_no,
                content,
                None if emb is None else "[" + ",".join(str(v) for v in emb) + "]",
            )
            for chunk_no, (content, emb) in enumerate(zip(chunks, embeddings, strict=True))
        ]
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO document_chunks
                    (project_id, doc_id, chunk_no, content, embedding)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (project_id, doc_id, chunk_no)
                DO UPDATE SET content = EXCLUDED.content,
                              embedding = EXCLUDED.embedding
                """,
                params_seq,
            )
        return len(chunks)

    def set_index_status(self, doc_id: str, status: str, *, error: str | None = None) -> None:
        """Transition ``index_status`` (``pending`` → ``indexed`` / ``error``)."""
        if status not in _VALID_INDEX_STATUSES:
            msg = f"invalid index_status {status!r}"
            raise ValueError(msg)
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE documents
                SET index_status = %s, index_error = %s
                WHERE project_id = %s AND doc_id = %s
                """,
                (status, error, self._project_id, doc_id),
            )

    def get(
        self, doc_id: str, *, include_content: bool = True
    ) -> tuple[DocumentMeta, bytes | None] | None:
        """Return ``(metadata, content_bytes)`` for *doc_id*, or ``None``.

        *include_content=False* skips fetching the BYTEA payload.
        """
        content_col = "d.content" if include_content else "NULL"
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT d.doc_id, d.title, d.content_type, d.size_bytes, d.sha256,
                       d.tags, d.index_status, d.index_error, d.retention,
                       d.agent_id, d.created_at, d.expires_at,
                       (SELECT count(*) FROM document_chunks c
                        WHERE c.project_id = d.project_id AND c.doc_id = d.doc_id),
                       {content_col}
                FROM documents d
                WHERE d.project_id = %s AND d.doc_id = %s
                """,  # content_col is a module-controlled literal, not user input
                (self._project_id, doc_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        meta = self._row_to_meta(row)
        content = bytes(row[13]) if include_content and row[13] is not None else None
        return meta, content

    def list_documents(self, *, tag: str | None = None, limit: int = 100) -> list[DocumentMeta]:
        """List document metadata (newest first), optionally filtered by *tag*."""
        if limit <= 0:
            return []
        where = "d.project_id = %s"
        params: list[Any] = [self._project_id]
        if tag:
            where += " AND %s = ANY(d.tags)"
            params.append(tag)
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT d.doc_id, d.title, d.content_type, d.size_bytes, d.sha256,
                       d.tags, d.index_status, d.index_error, d.retention,
                       d.agent_id, d.created_at, d.expires_at,
                       (SELECT count(*) FROM document_chunks c
                        WHERE c.project_id = d.project_id AND c.doc_id = d.doc_id)
                FROM documents d
                WHERE {where}
                ORDER BY d.created_at DESC
                LIMIT %s
                """,  # where is built from module-controlled fragments, not user input
                (*params, limit),
            )
            rows = cur.fetchall()
        return [self._row_to_meta(row) for row in rows]

    def delete(self, doc_id: str) -> bool:
        """Delete a document (chunks cascade).  Returns True when a row was removed."""
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM documents WHERE project_id = %s AND doc_id = %s",
                (self._project_id, doc_id),
            )
            deleted = int(cur.rowcount or 0) > 0
        if deleted:
            logger.debug("documents.deleted", project_id=self._project_id, doc_id=doc_id)
        return deleted

    def count(self) -> int:
        """Number of documents stored for this project."""
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM documents WHERE project_id = %s",
                (self._project_id,),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def total_bytes(self) -> int:
        """Sum of ``size_bytes`` across this project's documents."""
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(sum(size_bytes), 0) FROM documents WHERE project_id = %s",
                (self._project_id,),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Retrieval channels — fused by services.document_service with RRF
    # ------------------------------------------------------------------

    _SEARCH_JOIN = """
        FROM document_chunks c
        JOIN documents d
          ON d.project_id = c.project_id AND d.doc_id = c.doc_id
        WHERE c.project_id = %s
          AND d.index_status = 'indexed'
          AND (d.expires_at IS NULL OR d.expires_at > now())
    """

    def lexical_search(self, query: str, *, limit: int = 20) -> list[tuple[str, int, str, str]]:
        """tsvector channel: ``(doc_id, chunk_no, content, title)`` by ts_rank desc.

        Documents with ``index_status='error'`` (safety-flagged or failed
        embedding) and expired documents are never returned.
        """
        if not query or not query.strip() or limit <= 0:
            return []
        sql = (
            "SELECT c.doc_id, c.chunk_no, c.content, d.title, "
            "       ts_rank(c.search_vector, plainto_tsquery('english', %s)) AS _rank "
            + self._SEARCH_JOIN
            + "  AND c.search_vector @@ plainto_tsquery('english', %s) "
            "ORDER BY _rank DESC, c.doc_id, c.chunk_no "
            "LIMIT %s"
        )
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (query, self._project_id, query, limit))
            rows = cur.fetchall()
        return [(str(r[0]), int(r[1]), str(r[2]), str(r[3])) for r in rows]

    def semantic_search(
        self, query_embedding: list[float], *, limit: int = 20
    ) -> list[tuple[str, int, str, str]]:
        """pgvector channel: ``(doc_id, chunk_no, content, title)`` by cosine distance asc."""
        if not query_embedding or limit <= 0:
            return []
        vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"
        sql = (
            "SELECT c.doc_id, c.chunk_no, c.content, d.title "
            + self._SEARCH_JOIN
            + "  AND c.embedding IS NOT NULL "
            "ORDER BY c.embedding <=> %s::vector, c.doc_id, c.chunk_no "
            "LIMIT %s"
        )
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (self._project_id, vec_str, limit))
            rows = cur.fetchall()
        return [(str(r[0]), int(r[1]), str(r[2]), str(r[3])) for r in rows]

    # ------------------------------------------------------------------
    # Retention GC (TAP-5005)
    # ------------------------------------------------------------------

    def delete_expired(self) -> int:
        """Delete documents whose ``expires_at`` has passed.  Returns count.

        Chunks cascade via the FK.  Called from :meth:`MemoryStore.gc`.
        """
        with self._scoped_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM documents
                WHERE project_id = %s
                  AND expires_at IS NOT NULL
                  AND expires_at < now()
                """,
                (self._project_id,),
            )
            deleted = int(cur.rowcount or 0)
        if deleted:
            logger.info(
                "documents.gc_expired",
                project_id=self._project_id,
                deleted=deleted,
            )
        return deleted

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_meta(row: Any) -> DocumentMeta:  # noqa: ANN401
        def _iso(value: Any) -> str | None:  # noqa: ANN401
            if value is None:
                return None
            return value.isoformat() if hasattr(value, "isoformat") else str(value)

        return DocumentMeta(
            doc_id=str(row[0]),
            title=str(row[1]),
            content_type=str(row[2]),
            size_bytes=int(row[3]),
            sha256=str(row[4]),
            tags=list(row[5] or []),
            index_status=str(row[6]),
            index_error=row[7] if row[7] is None else str(row[7]),
            retention=str(row[8]),
            agent_id=str(row[9]),
            created_at=_iso(row[10]) or "",
            expires_at=_iso(row[11]),
            chunk_count=int(row[12] or 0),
        )
