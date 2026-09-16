"""Throwaway pgvector recall-comparison harness for TAP-7717 (PR #286 evidence).

Not part of the shipped package - lives outside src/. Connects only to a
standalone, uniquely-named pgvector-tap7717-* container it stands up itself;
never touches TAPPS_BRAIN_DATABASE_URL, port 8080, or any tapps-brain-* container.

Runs the same fixed 10-document corpus and query through the harness THREE
times to prove the comparison mechanism itself is trustworthy in both
directions, not just the one direction that shows "different":

  run1 - baseline: seed the unperturbed corpus, query, record ids.
  run2 - negative control: re-seed the SAME unperturbed corpus, query again.
         Asserts run1 == run2 (nothing changed -> harness reports identical).
  run3 - positive control: perturb one document, query again.
         Asserts run1 != run3 (something changed -> harness reports different).

If run1 != run2, the harness is non-deterministic and that is reported as a
failure (exit 1) rather than papered over - it would invalidate b3 entirely.

Reproduce from a clean checkout:

    $ docker run -d --name pgvector-tap7717-r2-$$ \\
        -e POSTGRES_PASSWORD=tapps -e POSTGRES_USER=tapps -e POSTGRES_DB=tapps_test \\
        -p 0:5432 pgvector/pgvector:pg17
    $ docker exec pgvector-tap7717-r2-$$ psql -U tapps -d tapps_test \\
        -c "CREATE EXTENSION IF NOT EXISTS vector;"
    $ docker port pgvector-tap7717-r2-$$ 5432   # note the mapped host port
    $ RECALL_HARNESS_DSN="postgresql://tapps:tapps@127.0.0.1:<mapped-port>/tapps_test" \\
        uv run python docs/evidence/recall-286-harness.py
    $ docker rm -f pgvector-tap7717-r2-$$        # including on failure
"""

from __future__ import annotations

import os
import sys

import psycopg
from sentence_transformers import SentenceTransformer

DSN = os.environ.get(
    "RECALL_HARNESS_DSN",
    "postgresql://tapps:tapps@127.0.0.1:5432/tapps_test",
)

MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"

CORPUS = [
    (1, "The pgvector extension adds vector similarity search to PostgreSQL."),
    (2, "HNSW indexes trade build time for faster approximate nearest neighbor queries."),
    (3, "Exponential decay reduces the relevance of stale memory entries over time."),
    (4, "BM25 ranking combines term frequency and inverse document frequency."),
    (5, "Reciprocal rank fusion merges results from multiple retrieval strategies."),
    (6, "Consolidation merges near-duplicate memories using Jaccard and TF-IDF similarity."),
    (7, "The sentence-transformers library wraps HuggingFace models for embeddings."),
    (8, "PostgreSQL row-level security isolates tenants by project_id and agent_id."),
    (9, "The Hive backend propagates memories across agents via LISTEN and NOTIFY."),
    (10, "Federation shares memories across projects through a dedicated backend."),
]

QUERY_TEXT = "How does the system rank and combine search results?"


def embed_model() -> SentenceTransformer:
    return SentenceTransformer(MODEL_NAME, revision=MODEL_REVISION)


def seed(conn: psycopg.Connection, model: SentenceTransformer, corpus: list[tuple[int, str]]) -> None:
    conn.execute("DROP TABLE IF EXISTS docs")
    conn.execute("CREATE TABLE docs (id INT PRIMARY KEY, text TEXT NOT NULL, embedding VECTOR(384))")
    for doc_id, text in corpus:
        vec = model.encode(text, normalize_embeddings=True).tolist()
        conn.execute(
            "INSERT INTO docs (id, text, embedding) VALUES (%s, %s, %s)",
            (doc_id, text, str(vec)),
        )
    conn.commit()


def query(conn: psycopg.Connection, model: SentenceTransformer, query_text: str, k: int = 10) -> list[int]:
    qvec = model.encode(query_text, normalize_embeddings=True).tolist()
    rows = conn.execute(
        "SELECT id FROM docs ORDER BY embedding <=> %s::vector LIMIT %s",
        (str(qvec), k),
    ).fetchall()
    return [r[0] for r in rows]


def corpus_size(conn: psycopg.Connection) -> int:
    return conn.execute("SELECT count(*) FROM docs").fetchone()[0]


def main() -> None:
    model = embed_model()
    with psycopg.connect(DSN, autocommit=False) as conn:
        print(f"st_version={__import__('sentence_transformers').__version__}")
        print(f"query_text={QUERY_TEXT!r}")

        # --- run1: baseline (unperturbed corpus) ---
        seed(conn, model, CORPUS)
        n1 = corpus_size(conn)
        run1_ids = query(conn, model, QUERY_TEXT)
        print(f"corpus_size={n1}")
        print(f"run1_ids={run1_ids}")
        assert n1 > 0, "positive control failed: corpus is empty"
        assert len(run1_ids) > 0, "positive control failed: query returned zero ids"

        # --- run2: negative control - re-seed the SAME unperturbed corpus ---
        seed(conn, model, CORPUS)
        run2_ids = query(conn, model, QUERY_TEXT)
        print(f"run2_ids={run2_ids}")

        # --- run3: positive control - perturb doc 5 (RRF doc) to an unrelated topic ---
        perturbed_corpus = list(CORPUS)
        perturbed_corpus[4] = (5, "Bananas are a good source of potassium and grow in tropical climates.")
        seed(conn, model, perturbed_corpus)
        run3_ids = query(conn, model, QUERY_TEXT)
        print(f"run3_ids={run3_ids}")

        negative_control = run1_ids == run2_ids
        positive_control = run1_ids != run3_ids
        print(f"negative_control_run1_eq_run2={'PASS' if negative_control else 'FAIL'}")
        print(f"positive_control_run1_ne_run3={'PASS' if positive_control else 'FAIL'}")

        if not negative_control:
            print("HARNESS_RESULT=NON_DETERMINISTIC -> BROKEN (run1 != run2 on identical input)")
            sys.exit(1)
        if not positive_control:
            print("HARNESS_RESULT=IDENTICAL_AFTER_PERTURBATION -> BROKEN")
            sys.exit(1)

        print("HARNESS_RESULT=RUN1_EQ_RUN2_AND_RUN1_NE_RUN3 -> OK")
        sys.exit(0)


if __name__ == "__main__":
    main()
