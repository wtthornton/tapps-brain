# DB Roles Runbook — tapps-brain

**Covers EPIC-063 STORY-063.1 + STORY-063.2: least-privilege Postgres roles.**

> **Backup & recovery:** See [Postgres Backup Guide](../guides/postgres-backup.md) and
> [Postgres Backup Runbook](./postgres-backup-runbook.md) for backup strategies,
> point-in-time recovery, and the Hive failover procedure.

## Roles overview

tapps-brain uses three Postgres roles to enforce least-privilege access:

| Role | What it can do | Who uses it |
|------|---------------|-------------|
| `tapps_migrator` | DDL: CREATE/ALTER/DROP tables, indexes, functions | Migration job (CI / deploy pipeline) |
| `tapps_runtime` | DML only: SELECT, INSERT, UPDATE, DELETE | Running application (`TAPPS_BRAIN_HIVE_DSN`) |
| `tapps_readonly` | SELECT only | Debugging, read replicas, reporting |

**Key rule: the application MUST connect as `tapps_runtime`, never as `tapps_migrator`
or a superuser.**

---

## Initial setup

### 1. Apply schema migrations (as `tapps_migrator` or superuser)

```bash
psql "$TAPPS_BRAIN_MIGRATOR_DSN" -f src/tapps_brain/migrations/hive/001_initial.sql
psql "$TAPPS_BRAIN_MIGRATOR_DSN" -f src/tapps_brain/migrations/federation/001_initial.sql
psql "$TAPPS_BRAIN_MIGRATOR_DSN" -f src/tapps_brain/migrations/private/001_initial.sql
```

### 2. Create roles and grant privileges (as superuser)

The roles migration requires superuser or `CREATEROLE` privilege:

```bash
psql "$TAPPS_BRAIN_SUPERUSER_DSN" -f src/tapps_brain/migrations/roles/001_db_roles.sql
```

Verify roles were created:

```sql
-- Check role list
SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname LIKE 'tapps_%';

-- Expected output:
--  tapps_migrator  | t
--  tapps_runtime   | t
--  tapps_readonly  | t
```

### 3. Set passwords for each role

```sql
-- Run as superuser
ALTER ROLE tapps_migrator  PASSWORD '<strong-password>';
ALTER ROLE tapps_runtime   PASSWORD '<strong-password>';
ALTER ROLE tapps_readonly  PASSWORD '<strong-password>';
```

**Store passwords in your secret manager (Vault, AWS Secrets Manager, K8s Secrets, etc.)
— never in code, config files, or CI logs.**

### 4. Configure application DSN

Set the runtime DSN (application):

```bash
# .env or secret injection — never commit real credentials
TAPPS_BRAIN_HIVE_DSN=postgres://tapps_runtime:<password>@<host>:<port>/<dbname>
```

Set the migrator DSN (deploy/CI jobs only):

```bash
TAPPS_BRAIN_MIGRATOR_DSN=postgres://tapps_migrator:<password>@<host>:<port>/<dbname>
```

---

## Applying migrations in production

Migration jobs MUST run as `tapps_migrator`, not `tapps_runtime`:

```bash
# In your deploy pipeline / Dockerfile.migrate
psql "$TAPPS_BRAIN_MIGRATOR_DSN" -f path/to/NNN_migration.sql
```

The application (`tapps_runtime`) does NOT have DDL rights and cannot apply migrations.
Attempting to do so will produce a permission error.

---

## CI configuration

In CI, use a single Postgres service container. Apply migrations using `tapps_migrator`
credentials, then run tests with `tapps_runtime`:

```yaml
# .github/workflows/ci.yml example
env:
  TAPPS_BRAIN_MIGRATOR_DSN: postgres://tapps_migrator:ci-password@localhost:5432/tapps_brain_test
  TAPPS_BRAIN_HIVE_DSN:     postgres://tapps_runtime:ci-password@localhost:5432/tapps_brain_test

steps:
  - name: Apply migrations
    run: |
      psql "$TAPPS_BRAIN_MIGRATOR_DSN" -f src/tapps_brain/migrations/hive/001_initial.sql
      psql "$TAPPS_BRAIN_MIGRATOR_DSN" -f src/tapps_brain/migrations/federation/001_initial.sql
      psql "$TAPPS_BRAIN_MIGRATOR_DSN" -f src/tapps_brain/migrations/private/001_initial.sql
      psql "$TAPPS_BRAIN_SUPERUSER_DSN" -f src/tapps_brain/migrations/roles/001_db_roles.sql

  - name: Run tests
    run: pytest tests/ -v
    env:
      TAPPS_BRAIN_HIVE_DSN: ${{ env.TAPPS_BRAIN_HIVE_DSN }}
```

---

## DSN hygiene — no credentials in logs

tapps-brain masks DSN values in logs and error messages. To verify no accidental
credential leakage exists in the codebase:

```bash
# Audit for logged DSN strings — should return no matches in runtime code
grep -r "TAPPS_BRAIN.*DSN\|hive_dsn\|migration.*dsn" src/tapps_brain/ \
  | grep -v "os.environ\|getenv\|env.get\|#\|test"
```

When loading DSNs at startup, always read from environment variables or a secret reference,
never from files committed to source control:

```python
# Good: read from environment
dsn = os.environ["TAPPS_BRAIN_HIVE_DSN"]

# Bad: hardcoded or logged
dsn = "postgres://tapps_runtime:secret@host/db"  # NEVER do this
logger.info(f"Connecting to {dsn}")              # NEVER log the raw DSN
```

If your secret manager returns a DSN URL, log only the host portion:

```python
from urllib.parse import urlparse

parsed = urlparse(dsn)
logger.info("Connecting to Postgres: host=%s db=%s", parsed.hostname, parsed.path.lstrip("/"))
```

---

## Read-only role usage

`tapps_readonly` is optional. Provision it when you need:

- A debugging connection for engineers without write access
- A read replica connection (streaming replication + read-only queries)
- A reporting/analytics query runner

```bash
TAPPS_BRAIN_READONLY_DSN=postgres://tapps_readonly:<password>@<host>/<dbname>
```

---

## Troubleshooting

### "permission denied for table …"

The application is connecting as a role that lacks DML privileges. Verify:

1. `TAPPS_BRAIN_HIVE_DSN` contains `tapps_runtime` credentials (not superuser or migrator).
2. `roles/001_db_roles.sql` was applied after all schema migrations.
3. If new tables were added after the roles migration, apply the next roles migration to
   grant on the new tables, or run:

   ```sql
   -- Run as superuser
   GRANT SELECT, INSERT, UPDATE, DELETE ON <new_table> TO tapps_runtime;
   ```

### "role tapps_runtime does not exist"

The roles migration has not been applied. Run:

```bash
psql "$TAPPS_BRAIN_SUPERUSER_DSN" -f src/tapps_brain/migrations/roles/001_db_roles.sql
```

### Re-applying is safe

The roles migration is fully idempotent. Re-applying it on a database that already has
roles and grants configured will produce informational notices but no errors.

---

## Security notes

- **`tapps_migrator`** credentials grant DDL rights — treat like a superuser. Rotate
  after every migration run in high-security environments.
- **`tapps_runtime`** has no DDL rights. A compromised runtime credential cannot drop
  tables or alter schema.
- **`tapps_readonly`** has no write rights. Safe for read-replica or analytics access.
- All three roles have `LOGIN` enabled. Disable login on any role you want to use purely
  as a privilege group: `ALTER ROLE tapps_readonly NOLOGIN`.
- Enforce TLS for all connections in production (`sslmode=require` or `verify-full`).

## Related docs

- `src/tapps_brain/migrations/README.md` — migration folder structure and apply order
- `docs/planning/adr/ADR-007-postgres-only-no-sqlite.md` — Postgres-only decision
- `docs/engineering/threat-model.md` — STRIDE threat model referencing DB roles
- `docs/guides/hive-deployment.md` — full Docker/K8s deployment guide
