# sqlite-vec index — operator playbook

This guide covers the **`memory_vec`** virtual table (**vec0** from [sqlite-vec](https://github.com/asg017/sqlite-vec)) used for KNN retrieval. sqlite-vec is a core dependency since v2.2.0 and is enabled by default. Implementation: `src/tapps_brain/sqlite_vec_index.py`, wired from `MemoryPersistence` in `persistence.py`.

## What gets indexed

- Rows are keyed by memory **`key`**; the vector column stores **float32** blobs built with `sqlite_vec.serialize_float32` from the same embedding list persisted in `memories.embedding` (JSON).
- Only embeddings whose length matches the compile-time dimension (**384** today, `DEFAULT_VEC_DIM` in `sqlite_vec_index.py`) are indexed. Shorter/longer vectors are skipped on sync; clearing an embedding removes the vec row.

## Distance metric vs SQL

- The table is created as:

  ```sql
  CREATE VIRTUAL TABLE memory_vec USING vec0(
    key TEXT,
    embedding float[384]
  );
  ```

  No `distance_metric=` clause is set, so sqlite-vec uses its **default for `float[]` columns: L2 (Euclidean) distance**. KNN queries use:

  ```sql
  SELECT key, distance
  FROM memory_vec
  WHERE embedding MATCH ? AND k = ?
  ```

  **`distance` is L2 distance; lower is better (closer).** See upstream sqlite-vec docs for `vec0` and `MATCH`.

- Default embeddings are **L2-normalized** (see [`embedding-model-card.md`](embedding-model-card.md)). For unit vectors, ranking by L2 distance is **order-equivalent** to ranking by cosine similarity (both prefer directions aligned with the query).

- Hybrid retrieval maps raw distance to a bounded score for fusion: `retrieval.py` uses `sim = 1.0 / (1.0 + distance)` before RRF. That is a **monotonic** transform for distance ≥ 0; it is not literal cosine similarity.

## Incremental index maintenance (save-path cost)

- On each persisted memory write, `_sqlite_vec_sync_unlocked` in `persistence.py` either **deletes** the row for that key or **replaces** it via `upsert_vec_row` (implemented as **DELETE + INSERT** with a new `rowid` in `sqlite_vec_index.py`).
- There is **no batching**: high churn (e.g. consolidation or many small saves) means many small vec0 updates serialized with the main store lock (or writer connection).
- **Cost model (qualitative):** each upsert is O(1) row ops plus sqlite-vec ANN structure maintenance; expect **higher write amplification** than a plain `UPDATE` on a regular table. Profile if save latency spikes when sqlite-vec is enabled at large N.

## Rebuild playbook

Use this when the vec index is **corrupt**, **empty after a bad migration**, **out of sync** with `memories.embedding`, or after changing **embedding dimension** / **model** in a way that invalidates stored vectors.

1. **Quiesce writers** — stop MCP servers, CLIs, and any process holding `memory.db` open so SQLite is not busy (see [`sqlite-database-locked.md`](sqlite-database-locked.md)).
2. **Backup** — copy `memory.db` and, if present, `-wal` / `-shm` siblings (SQLCipher: use your normal encrypted backup procedure).
3. **Drop the virtual table** (plain SQLite example):

   ```sql
   DROP TABLE IF EXISTS memory_vec;
   ```

   Run via `sqlite3 memory.db`, or any tool that loads the same extensions/key as production.

4. **Restart** the store (or reopen `MemoryPersistence`). On startup, `ensure_memory_vec_table` recreates `memory_vec`, and **`maybe_backfill_if_empty`** repopulates from rows where `memories.embedding` is non-empty and length matches `DEFAULT_VEC_DIM`.
5. **Mixed models / dimensions** — schema **`embedding_model_id`** (v17) helps audit which rows used which model; rebuilding embeddings in the DB still requires an application-level re-embed + save (not covered here). Until then, only rows matching the current dim are indexed.

If the extension fails to load, sqlite-vec stays disabled and retrieval falls back to non-KNN vector paths; dropping `memory_vec` alone does not remove embeddings from `memories`.

## VACUUM and fragmentation

- **`VACUUM`** rebuilds the main database file and can shrink space after large deletes. It requires **exclusive access** and can be slow on big files.
- With **WAL** mode, consider **`PRAGMA wal_checkpoint(TRUNCATE)`** (or `PASSIVE`) before offline maintenance so `-wal` does not surprise operators; long-lived MCP processes are discussed in [`sqlite-database-locked.md`](sqlite-database-locked.md) and `docs/engineering/system-architecture.md` § concurrency.

## Read-only search connection

When **`TAPPS_SQLITE_MEMORY_READONLY_SEARCH`** is set, KNN uses a **separate read-only** connection (see `persistence.py`). After a **full rebuild** on the writer, restart clients using the RO path so they open a fresh read connection and see the new `memory_vec` snapshot.

## See also

- [`embedding-model-card.md`](embedding-model-card.md) — defaults, normalization, performance backlog.
- [`data-stores-and-schema.md`](../engineering/data-stores-and-schema.md) — store layout.
- [`features-and-technologies.md`](../engineering/features-and-technologies.md) § retrieval table.
