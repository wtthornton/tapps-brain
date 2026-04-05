# SQLCipher (optional at-rest encryption)

> **WARNING: Lost key = data loss.** If you lose the SQLCipher passphrase, the encrypted database **cannot be recovered**. There is no key escrow, no backdoor, and no recovery mechanism in the library. Treat your passphrase with the same rigor as production database credentials. Store it in a secrets manager.

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

`MemoryStore.health()` includes `sqlcipher_enabled` (bool) and, when the profile sets `seeding.seed_version`, `profile_seed_version`. The text CLI `tapps-brain maintenance health` prints `SQLCipher: enabled|disabled` and the profile seed line when present.

## Fallback behavior

- No env var and no `encryption_key` argument → standard `sqlite3` (unchanged from pre-#23 behavior).
- Hive without hive-specific key uses the project memory key from env when set, so one passphrase can cover both; use `TAPPS_BRAIN_HIVE_ENCRYPTION_KEY` when Hive should differ.

## Lost passphrase

**If you lose the SQLCipher passphrase, the encrypted database cannot be recovered.** There is no escrow in the library. Store passphrases in a secrets manager or team policy with the same rigor as production credentials.

## Backup and restore verification

Use this checklist for **project `memory.db`** and, when encrypted, **`~/.tapps-brain/hive/hive.db`**. Repeat on a schedule that matches your compliance needs (e.g. quarterly verification on a staging host).

1. **Quiesce writers** — stop or idle MCP/CLI sessions that write the target DB so the file is consistent (or snapshot at rest and accept point-in-time semantics).
2. **Copy artifacts** — back up the encrypted `.db` file(s) and the rest of the store directory your ops standard requires (e.g. `.tapps-brain/memory/` including `memory_log.jsonl` if you rely on audit replay).
3. **Verify restore** — on a **non-production** copy: set `TAPPS_BRAIN_ENCRYPTION_KEY` (and `TAPPS_BRAIN_HIVE_ENCRYPTION_KEY` if Hive uses a different key), open the store, run `tapps-brain maintenance health` or a read/search smoke test against known keys.
4. **Re-key drill (optional)** — on a **copy** of production data, run `tapps-brain maintenance rekey-db` with `--old-passphrase` / `--new-passphrase` to validate rotation procedures and key material handling.

After `encrypt-db`, keep the original plain file only until you have verified the encrypted copy opens with the intended key; then remove or archive the plain backup per policy.

## Enterprise key handling (KMS / envelope patterns)

Core tapps-brain expects a **passphrase** (env or `encryption_key=`). **Wrapping a data-encryption key with a cloud KMS or HSM**, or injecting the passphrase from a sidecar agent, is **deployment-specific** — implement in your secrets layer before the process starts. The project does not ship vendor-specific KMS integration; maintainer stance: [`ADR-005`](../planning/adr/ADR-005-sqlcipher-key-backup-operations.md).

The recommended pattern for KMS integration:

1. A wrapper script or init container retrieves the data-encryption key from the KMS (AWS KMS, GCP Cloud KMS, Azure Key Vault, HashiCorp Vault, etc.).
2. The wrapper exports `TAPPS_BRAIN_ENCRYPTION_KEY` into the process environment.
3. `tapps-brain` reads the passphrase from the environment variable as usual.

This keeps the KMS-specific logic outside the library boundary and avoids vendor lock-in.

## CI test matrix

Tests marked with `@pytest.mark.requires_encryption` exercise SQLCipher-specific code paths (encrypt, decrypt, rekey, encrypted open). These tests are **skipped automatically** when `pysqlcipher3` is not installed, which is the typical CI configuration since building `pysqlcipher3` requires a system SQLCipher library.

To run encryption tests in CI, add a job variant that installs the system SQLCipher library and the `[encryption]` extra:

```yaml
# Example GitHub Actions step
- run: sudo apt-get install -y libsqlcipher-dev
- run: uv sync --extra dev --extra encryption
- run: uv run pytest -m requires_encryption -v
```

All non-encryption tests pass without `pysqlcipher3` installed.
