# SQLCipher (optional at-rest encryption)

`tapps-brain` can open project `memory.db`, feedback/diagnostics tables on that file, and the Hive `hive.db` with **SQLCipher** when a passphrase is configured. Plain SQLite remains the default so CI and minimal installs stay dependency-light.

## Install

1. Install the Python extra:

   ```bash
   uv sync --extra dev --extra encryption
   ```

2. **System SQLCipher**: many `pysqlcipher3` wheels expect a system `sqlcipher` library (package name varies by OS). If import fails or `PRAGMA cipher_version` is empty, install SQLCipher for your platform and retry.

## Key management

| Variable | Purpose |
|----------|---------|
| `TAPPS_BRAIN_ENCRYPTION_KEY` | Passphrase for `{project}/.tapps-brain/memory/memory.db` (and shared secondaries below unless overridden). |
| `TAPPS_BRAIN_HIVE_ENCRYPTION_KEY` | Optional passphrase **only** for `~/.tapps-brain/hive/hive.db`. If unset, Hive uses `TAPPS_BRAIN_ENCRYPTION_KEY`. |

Passphrase can also be passed programmatically:

- `MemoryPersistence(..., encryption_key="...")`
- `MemoryStore(..., encryption_key="...")`
- `HiveStore(..., encryption_key="...")`

`FeedbackStore` and `DiagnosticsHistoryStore` receive the same key as `MemoryPersistence` automatically when created via `MemoryStore`.

If a key is set but `pysqlcipher3` is not installed, opening the store raises `ImportError` with install hints.

## Encrypting or migrating an existing plain database

Use the CLI (requires `[cli]` + `[encryption]`):

```bash
# Copy plain memory.db to an encrypted sibling (default: memory.db.encrypted)
tapps-brain maintenance encrypt-db --project-dir . --passphrase 'your-secret'

# Or set TAPPS_BRAIN_ENCRYPTION_KEY and omit --passphrase (prompted if unset)
```

After verifying the encrypted file, back up the original, replace `memory.db`, and keep `TAPPS_BRAIN_ENCRYPTION_KEY` set (or pass `encryption_key=` in code).

**Decrypt** (export to plain SQLite):

```bash
tapps-brain maintenance decrypt-db --project-dir . -o /path/to/plain.db --passphrase 'your-secret'
```

**Rekey** (rotate passphrase in place):

```bash
tapps-brain maintenance rekey-db --project-dir . --old-passphrase 'old' --new-passphrase 'new'
```

## Health / observability

`MemoryStore.health()` includes `sqlcipher_enabled` (bool). The text CLI `tapps-brain maintenance health` prints `SQLCipher: enabled|disabled`.

## Fallback behavior

- No env var and no `encryption_key` argument → standard `sqlite3` (unchanged from pre-#23 behavior).
- Hive without hive-specific key uses the project memory key from env when set, so one passphrase can cover both; use `TAPPS_BRAIN_HIVE_ENCRYPTION_KEY` when Hive should differ.
