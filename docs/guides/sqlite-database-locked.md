# SQLite “database is locked” — operator runbook

tapps-brain uses SQLite with **WAL** journal mode for project memory, Hive, federation hub, feedback, and diagnostics stores. Under contention you may see **`sqlite3.OperationalError: database is locked`** (or host messages that wrap it).

## Quick triage

```mermaid
flowchart TD
  A[See database is locked] --> B{One process or many?}
  B -->|Many writers / scripts| C[Reduce parallelism or serialize jobs]
  B -->|Single MCP server, still errors| D[Increase TAPPS_SQLITE_BUSY_MS]
  D --> E{Still failing?}
  E -->|Yes| F[Check external tools holding the DB]
  E -->|No| G[Done]
  F --> H[Close DB browser / backup tools / second IDE]
  H --> I{Corruption or NFS locks?}
  I -->|Unusual FS / network disk| J[Move project to local disk]
  I -->|Clean| K[See concurrency doc]
```

## Tune: `TAPPS_SQLITE_BUSY_MS`

All connections opened via `connect_sqlite` (memory, Hive, feedback, diagnostics) and the **federation hub** apply:

`PRAGMA busy_timeout = <ms>`

| Variable | Meaning |
| -------- | ------- |
| `TAPPS_SQLITE_BUSY_MS` | Wait up to this many **milliseconds** for a locked page before returning `SQLITE_BUSY`. |
| *(unset or invalid)* | **5000** ms (5 s). |
| Valid range | **0** … **3600000** (0 = fail fast; upper bound avoids absurd waits). |

**Examples:**

```bash
# Linux / macOS — longer wait before surfacing errors (e.g. heavy parallel CLI)
export TAPPS_SQLITE_BUSY_MS=30000
```

Restart the MCP server or CLI after changing the environment.

**Interaction with app locks:** `MemoryStore` still serializes most work with a **process-local** lock. SQLite busy handling helps when multiple **SQLite** users overlap (WAL readers/writers, federation, or rare paths that briefly contend at the engine). It does not remove the single-lane store lock; see [`system-architecture.md`](../engineering/system-architecture.md) § *Concurrency model*.

## Other causes

- **Second process** opening the same `memory.db` (backup utility, `sqlite3` CLI, another agent).
- **SQLCipher** mis-key or mixed plain/encrypted opens — see [`sqlcipher.md`](sqlcipher.md).
- **Very slow disk** or **network-backed** project roots — WAL still needs reliable file locking.

## Read-only connection pool (spike / future)

Today each store uses a **single** SQLite connection per process (plus internal locks). A **separate read connection** for search-only paths could overlap reads with writes more at the SQLite layer, but it must be designed with the **store** and **persistence** lock ordering rules to avoid deadlocks and torn reads. That work is tracked as a **spike** under **EPIC-050** STORY-050.3; there is no separate read pool in the product yet.

## Related documentation

- Concurrency and lock timeout: [`system-architecture.md`](../engineering/system-architecture.md) § *Concurrency model* (`TAPPS_STORE_LOCK_TIMEOUT_S`).
- Encrypted DBs: [`sqlcipher.md`](sqlcipher.md).
