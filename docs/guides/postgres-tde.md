# pg_tde Operator Runbook

**Applies to:** Percona Distribution for PostgreSQL 17 + pg_tde 2.1.2 (released 2026-03-02)
**Audience:** Production operators responsible for enabling at-rest encryption on a
tapps-brain Postgres deployment.

> **Important:** pg_tde is a Percona Distribution extension. It does **not** ship with
> vanilla PostgreSQL 17 or the `pgvector/pgvector:pg17` Docker image used in development.
> Production use requires either Percona Distribution for PostgreSQL (PDPG) 17 or one of
> the cloud-provider TDE equivalents listed in [Cloud Provider TDE Fallback](#cloud-provider-tde-fallback).

---

## Table of Contents

1. [Why pg_tde](#why-pg_tde)
2. [Prerequisites](#prerequisites)
3. [Install Percona Distribution for PostgreSQL 17](#install-percona-distribution-for-postgresql-17)
4. [Enable pg_tde Extension](#enable-pg_tde-extension)
5. [Key Provider Configuration](#key-provider-configuration)
   - [HashiCorp Vault](#hashicorp-vault)
   - [OpenBao (open-source Vault fork)](#openbao-open-source-vault-fork)
   - [File-based key (dev / air-gapped)](#file-based-key-dev--air-gapped)
6. [Encrypt Tables and WAL](#encrypt-tables-and-wal)
7. [Key Rotation Procedure](#key-rotation-procedure)
8. [Verifying Encryption](#verifying-encryption)
9. [Troubleshooting](#troubleshooting)
10. [Cloud Provider TDE Fallback](#cloud-provider-tde-fallback)
11. [Operational Checklist](#operational-checklist)
12. [References](#references)

---

## Why pg_tde

tapps-brain's [ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md) replaced the
former SQLCipher-based per-file encryption with database-level TDE. The design decision is:

> **At-rest encryption is the storage layer's job.** Application code does not handle keys.

pg_tde 2.1.2 is the first open-source, production-ready TDE for PostgreSQL that encrypts:
- **Heap pages** (table data)
- **WAL** (write-ahead log — prevents data leakage via WAL shipping or PITR)
- **System catalogs** when enabled

For cloud-hosted deployments where Percona Distribution is not available, see
[Cloud Provider TDE Fallback](#cloud-provider-tde-fallback).

---

## Prerequisites

| Requirement | Version |
|-------------|---------|
| OS | Ubuntu 22.04 LTS / Debian 12 / RHEL 9 (x86_64 or arm64) |
| Postgres | **Percona Distribution for PostgreSQL 17** (PDPG 17) — NOT vanilla pg17 |
| pg_tde | 2.1.2+ (bundled in PDPG 17 repo) |
| pgvector | 0.7.0+ (available in PDPG 17 repos) |
| Key store | HashiCorp Vault ≥ 1.15, OpenBao ≥ 2.0, or file-based (dev only) |
| tapps-brain | 3.4.0+ (Postgres-only persistence plane; ADR-007 complete) |

> **Note:** The development Docker image (`pgvector/pgvector:pg17`) is built on vanilla
> PostgreSQL 17 and **cannot** load pg_tde. Use it for local development only. In
> production, replace or extend it with the PDPG 17 image (see
> `docker/docker-compose.hive.yaml` comments).

---

## Install Percona Distribution for PostgreSQL 17

### Ubuntu 22.04 / Debian 12

```bash
# 1. Install Percona release tool
wget https://repo.percona.com/apt/percona-release_latest.generic_all.deb
sudo apt install -y ./percona-release_latest.generic_all.deb

# 2. Enable PDPG 17 repo (includes pg_tde and pgvector packages)
sudo percona-release setup ppg-17

# 3. Install server, pg_tde, pgvector
sudo apt install -y \
  percona-postgresql-17 \
  percona-postgresql-17-pg-tde \
  percona-postgresql-17-pgvector

# 4. Verify pg_tde shared library is present
ls /usr/lib/postgresql/17/lib/pg_tde.so
```

### RHEL 9 / Rocky Linux 9

```bash
# 1. Install Percona release tool
sudo yum install -y https://repo.percona.com/yum/percona-release-latest.noarch.rpm

# 2. Enable repo
sudo percona-release setup ppg-17

# 3. Install
sudo yum install -y \
  percona-postgresql17 \
  percona-postgresql17-pg_tde \
  percona-postgresql17-pgvector

ls /usr/pgsql-17/lib/pg_tde.so
```

### Docker (production image)

Extend the reference tapps-brain Docker image to use PDPG 17:

```dockerfile
FROM perconalab/percona-distribution-postgresql:17 AS base

# pgvector is available in PDPG 17 repos; install pg_tde
RUN apt-get update && apt-get install -y \
    percona-postgresql-17-pg-tde \
    percona-postgresql-17-pgvector \
  && rm -rf /var/lib/apt/lists/*
```

> For the Hive stack, replace `pgvector/pgvector:pg17` in
> `docker/docker-compose.hive.yaml` with this production image.

---

## Enable pg_tde Extension

Add `pg_tde` to `shared_preload_libraries` **before** creating the extension:

```ini
# /etc/postgresql/17/main/postgresql.conf  (PDPG layout)
shared_preload_libraries = 'pg_tde'
```

Then restart Postgres and create the extension:

```sql
-- Run as superuser on the tapps_brain database
ALTER SYSTEM SET shared_preload_libraries = 'pg_tde';
SELECT pg_reload_conf();  -- requires restart for shared_preload_libraries
```

```bash
sudo systemctl restart postgresql@17-main
```

```sql
-- After restart
\c tapps_brain
CREATE EXTENSION IF NOT EXISTS pg_tde;
```

Verify:

```sql
SELECT extname, extversion FROM pg_extension WHERE extname = 'pg_tde';
--  extname | extversion
-- ---------+------------
--  pg_tde  | 2.1.2
```

---

## Key Provider Configuration

pg_tde supports pluggable key providers. The recommended provider for production is
**Vault** or **OpenBao**. File-based keys are acceptable for air-gapped or dev environments.

### HashiCorp Vault

#### Vault setup

```bash
# Enable the transit secrets engine (recommended over kv for key management)
vault secrets enable transit

# Create a key for tapps-brain
vault write -f transit/keys/tapps-brain-master type=aes256-gcm96

# Create a policy that allows the Postgres server to use the key
vault policy write tapps-pg-tde - <<EOF
path "transit/encrypt/tapps-brain-master" { capabilities = ["update"] }
path "transit/decrypt/tapps-brain-master" { capabilities = ["update"] }
path "transit/keys/tapps-brain-master"    { capabilities = ["read"] }
EOF

# Issue an AppRole credential for the Postgres server
vault auth enable approle
vault write auth/approle/role/tapps-pg-tde \
  token_policies="tapps-pg-tde" \
  token_ttl=1h \
  token_max_ttl=24h

vault read auth/approle/role/tapps-pg-tde/role-id
vault write -f auth/approle/role/tapps-pg-tde/secret-id
```

#### Postgres key provider registration

```sql
\c tapps_brain

SELECT pg_tde_add_key_provider_vault_v2(
  'vault-provider',                           -- provider name (arbitrary)
  'https://vault.internal:8200',              -- Vault address
  'transit',                                  -- secrets engine mount
  'tapps-brain-master',                       -- key name
  '<ROLE_ID>',                                -- AppRole role_id
  '<SECRET_ID>'                               -- AppRole secret_id
);

-- Set as the default master key for the database
SELECT pg_tde_set_principal_key('tapps-brain-master', 'vault-provider');
```

> **Secret management:** Store `ROLE_ID` and `SECRET_ID` in your secret manager (AWS
> Secrets Manager, Vault itself, etc.) and inject them at container start via env vars
> rather than hard-coding in SQL scripts.

### OpenBao (open-source Vault fork)

OpenBao 2.0+ is API-compatible with HashiCorp Vault. The pg_tde `vault_v2` provider
works with OpenBao without modification — replace the Vault address with your OpenBao
endpoint:

```sql
SELECT pg_tde_add_key_provider_vault_v2(
  'openbao-provider',
  'https://bao.internal:8200',   -- OpenBao address
  'transit',
  'tapps-brain-master',
  '<ROLE_ID>',
  '<SECRET_ID>'
);

SELECT pg_tde_set_principal_key('tapps-brain-master', 'openbao-provider');
```

OpenBao self-hosted is the recommended default for new deployments where Vault Enterprise
licensing is a concern.

### File-based key (dev / air-gapped)

> **Warning:** File-based keys are stored on the same host as the data. This provides
> protection against cold-storage theft only, not against a compromised host. Do not use
> in internet-facing production environments.

```sql
SELECT pg_tde_add_key_provider_file(
  'file-provider',
  '/etc/tapps-brain/master.key'   -- must be readable by the postgres OS user
);

SELECT pg_tde_set_principal_key('tapps-brain-file-key', 'file-provider');
```

Generate the key file:

```bash
openssl rand -base64 32 > /etc/tapps-brain/master.key
chown postgres:postgres /etc/tapps-brain/master.key
chmod 400 /etc/tapps-brain/master.key
```

---

## Encrypt Tables and WAL

Once a principal key is set, convert tapps-brain tables to use TDE:

```sql
\c tapps_brain

-- Private memory tables
ALTER TABLE private_memories          USING tde;
ALTER TABLE private_feedback_events   USING tde;
ALTER TABLE private_session_chunks    USING tde;
ALTER TABLE private_diagnostics_hist  USING tde;
ALTER TABLE private_audit_log         USING tde;

-- Hive tables (if co-located on the same cluster)
ALTER TABLE hive_memories             USING tde;
ALTER TABLE hive_entries              USING tde;
ALTER TABLE agent_registry            USING tde;

-- Federation tables
ALTER TABLE federation_memories       USING tde;
```

> `ALTER TABLE … USING tde` rewrites the table in-place. On a large deployment, run
> during a maintenance window and expect ~1–2 minutes per GB of data.

### Enable WAL encryption

WAL encryption protects data streamed to replicas and PITR backups:

```sql
-- Must be set before the next checkpoint after key provisioning
SELECT pg_tde_enable_wal_encryption();

-- Verify
SELECT pg_tde_is_wal_encrypted();
--  pg_tde_is_wal_encrypted
-- -------------------------
--  t
```

---

## Key Rotation Procedure

Key rotation in pg_tde is online (no downtime required for WAL encryption; heap
re-encryption runs in the background).

### Planned rotation

```bash
# Estimated downtime: ZERO for WAL key; background rewrite for heap pages (~10–30 min/GB)
```

```sql
\c tapps_brain

-- 1. Add the new key version in Vault/OpenBao (Vault rotates in-place)
-- vault write -f transit/keys/tapps-brain-master/rotate

-- 2. Tell pg_tde to switch to the new version
SELECT pg_tde_rotate_principal_key(
  'tapps-brain-master-v2',   -- new principal key name
  'vault-provider'           -- same provider
);

-- 3. Monitor background re-encryption progress
SELECT * FROM pg_tde_reencryption_status();
--  relname               | progress | total_pages | done_pages
-- -----------------------+----------+-------------+------------
--  private_memories      | 65       | 1024        | 665
--  ...
```

### Emergency rotation (key compromise)

1. Revoke the compromised key in Vault/OpenBao immediately.
2. Provision a new key (`vault write -f transit/keys/tapps-brain-emergency`).
3. Register and set the new key in Postgres:
   ```sql
   SELECT pg_tde_add_key_provider_vault_v2(
     'vault-provider-emergency',
     'https://vault.internal:8200',
     'transit', 'tapps-brain-emergency',
     '<NEW_ROLE_ID>', '<NEW_SECRET_ID>'
   );
   SELECT pg_tde_rotate_principal_key('tapps-brain-emergency', 'vault-provider-emergency');
   ```
4. Wait for `pg_tde_reencryption_status()` to report all tables complete.
5. Take a fresh base backup after re-encryption is complete (old backups used the
   compromised key and must be treated as potentially exposed).
6. Notify your security team per your incident response plan.

**Expected downtime:** None for connections; background re-encryption adds ~5–15% I/O
overhead during the rewrite window.

---

## Verifying Encryption

```sql
-- Check which tables are TDE-encrypted
SELECT relname, pg_tde_is_encrypted(oid) AS encrypted
FROM pg_class
WHERE relkind = 'r'
  AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
ORDER BY relname;

-- Check WAL encryption status
SELECT pg_tde_is_wal_encrypted();

-- List registered key providers
SELECT * FROM pg_tde_list_all_key_providers();
```

Physical verification (should show non-ASCII binary, not plaintext JSON):

```bash
# Inspect the heap file for private_memories (replace with actual OID)
psql tapps_brain -c "SELECT relfilenode FROM pg_class WHERE relname = 'private_memories';"
# Then:
strings /var/lib/postgresql/17/main/base/<db_oid>/<relfilenode> | head -20
# Encrypted output: you should NOT see memory key/value strings in plaintext
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `ERROR: pg_tde is not loaded` | `shared_preload_libraries` not set | Add `pg_tde` to `postgresql.conf` and restart |
| `ERROR: no principal key set` | Key provider registered but `pg_tde_set_principal_key` not called | Call `pg_tde_set_principal_key(...)` |
| `connection refused` to Vault | Vault sealed or network unreachable | `vault status`; check firewall; unseal if needed |
| `permission denied` on key path | File key not readable by `postgres` OS user | `chown postgres:postgres /path/to/key && chmod 400` |
| `ALTER TABLE … USING tde` takes too long | Large table with many pages | Run during off-peak; monitor with `pg_tde_reencryption_status()` |
| Standby lag spike during rotation | Background re-encryption generates extra WAL | Expected; monitor replication lag; throttle if needed |
| `pg_tde_rotate_principal_key` fails mid-rotation | Vault unreachable mid-job | Fix Vault connectivity; re-run rotate — pg_tde is idempotent |

---

## Cloud Provider TDE Fallback

When Percona Distribution for PostgreSQL is not available (managed services), use the
cloud provider's native TDE. These are operationally simpler but lock the deployment to
one provider.

| Provider | Service | TDE Feature | Key Management | Notes |
|----------|---------|-------------|----------------|-------|
| **AWS** | RDS for PostgreSQL 17 | [AWS RDS Encryption](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Overview.Encryption.html) | AWS KMS (CMK or AWS-managed) | Enabled at instance creation; cannot be disabled after creation. Snapshots inherit encryption. pgvector 0.7.0+ available on RDS pg17. |
| **Google Cloud** | Cloud SQL for PostgreSQL 15/16 | [Cloud SQL CMEK](https://cloud.google.com/sql/docs/postgres/cmek) | Cloud KMS (CMEK or Google-managed) | CMEK enabled at instance creation. pgvector is a Cloud SQL extension. Key rotation via Cloud KMS key version management. |
| **Azure** | Azure Database for PostgreSQL Flexible Server | [Azure Disk Encryption + CMK](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/concepts-data-encryption) | Azure Key Vault (customer-managed key) | CMK configured post-creation. pgvector 0.5.0+ available; upgrade to 0.7.0+ may require explicit version pinning. |
| **Supabase** | Supabase (managed pg17) | AES-256 at rest (infrastructure-level) | Supabase-managed; BYOK not yet GA | Encryption is on by default; operator-managed CMK not available as of 2026-Q1. |
| **Neon** | Neon Serverless PostgreSQL | AES-256 at rest (storage-level) | Neon-managed; CMEK roadmap | Encryption on by default. Note: Neon stopped offering `pg_search` for new projects (2026-03-19); existing projects unaffected. |

### Choosing between pg_tde and cloud TDE

| Factor | pg_tde (self-hosted) | Cloud provider TDE |
|--------|----------------------|-------------------|
| Key control | Full (your Vault/OpenBao) | Shared with provider |
| Operator complexity | Medium (PDPG install + Vault) | Low (toggle at creation) |
| WAL encryption | Yes (pg_tde) | Varies (RDS: yes; others: storage-only) |
| Portability | High (any Linux server) | Low (provider lock-in) |
| Compliance | PCI-DSS, HIPAA-ready with Vault | Provider-specific certifications |

---

## Operational Checklist

Before going to production with TDE enabled:

- [ ] Percona Distribution for PostgreSQL 17 (not vanilla pg17) is installed on all DB hosts
- [ ] `shared_preload_libraries = 'pg_tde'` set and Postgres restarted
- [ ] `CREATE EXTENSION pg_tde` executed on `tapps_brain` database
- [ ] Key provider (Vault / OpenBao / file) registered and tested
- [ ] Principal key set (`pg_tde_set_principal_key`)
- [ ] All tapps-brain tables converted with `ALTER TABLE … USING tde`
- [ ] WAL encryption enabled (`pg_tde_enable_wal_encryption`)
- [ ] Encryption verified (`pg_tde_is_encrypted`, physical `strings` check)
- [ ] Key rotation tested in staging with `pg_tde_rotate_principal_key`
- [ ] Emergency rotation runbook reviewed with on-call team
- [ ] Base backup taken *after* TDE is fully enabled (pre-TDE backups are unencrypted)
- [ ] Backup restore tested from TDE-encrypted backup
- [ ] Key provider connectivity monitored (alert if Vault unreachable)

---

## References

- [ADR-007: PostgreSQL-only persistence plane](../planning/adr/ADR-007-postgres-only-no-sqlite.md)
- [Hive Deployment Guide](./hive-deployment.md)
- [Postgres DSN Configuration](./postgres-dsn.md)
- [Threat Model](../engineering/threat-model.md)
- [Percona Distribution for PostgreSQL 17 — pg_tde docs](https://docs.percona.com/postgresql/17/pg-tde.html)
- [Percona pg_tde 2.1.2 release notes (2026-03-02)](https://www.percona.com/doc/postgresql/17/release-notes/pg-tde-2.1.2.html)
- [OpenBao project](https://openbao.org/)
- [pgvector on Hive deployment](./hive-deployment.md)
