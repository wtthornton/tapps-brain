# ADR-005: SQLCipher operations — passphrase runbook + backup verification (defer KMS product integration)

**Status:** Superseded by [ADR-007](./ADR-007-postgres-only-no-sqlite.md) (2026-04-11) — SQLCipher and the `[encryption]` extra were removed along with SQLite. At-rest encryption is now handled at the storage layer via Percona `pg_tde`. See [`postgres-tde.md`](../../guides/postgres-tde.md).  
**Date:** 2026-04-03  
**Owner:** @wtthornton  
**Epic / story:** [EPIC-051](../epics/EPIC-051.md) — STORY-051.5  
**Context:** [features-and-technologies.md](../../engineering/features-and-technologies.md) section 10 checklist item 5

## Context

Checklist item 5 asks for a **key management and backup** story for operators using optional **SQLCipher** (`[encryption]` extra, GitHub **#23**).

Shipped baseline:

- Env vars `TAPPS_BRAIN_ENCRYPTION_KEY` / `TAPPS_BRAIN_HIVE_ENCRYPTION_KEY`, programmatic `encryption_key=`, `sqlcipher_util.py`, encrypted paths in persistence / Hive / feedback / diagnostics.
- CLI **`maintenance encrypt-db`**, **`decrypt-db`**, **`rekey-db`**; health reports **`sqlcipher_enabled`**.
- Guide `sqlcipher.md` *(guide removed — SQLite retired in ADR-007)*.

**EPIC-043** STORY-043.6 research notes mention **KMS/HSM** and **backup** portability as operator-critical.

## Decision

1. **Shipped / maintained path (do):** Keep **passphrase-based** SQLCipher as the **only** first-class encryption mode in core. Operators use **env / explicit key**, **CLI migrate and rekey**, and the **expanded** `sqlcipher.md` *(guide removed — SQLite retired in ADR-007)* sections: **lost passphrase = data loss**, **backup + restore verification checklist**, and a short **enterprise** note that KMS envelope patterns are **host-owned**.

2. **Documentation delivered with this ADR:** `sqlcipher.md` *(guide removed — SQLite retired in ADR-007)* gains explicit **backup / restore verification** steps and **key-loss** warning so the runbook matches checklist expectations without new code.

3. **Out of scope for core / deferred (not shipping now):**
   - **Vendor-specific** “envelope encryption” how-tos (AWS KMS, GCP KMS, Azure Key Vault, HSM PKCS#11) as **canonical** project docs — integrators follow their platform’s patterns to **inject** a passphrase or unlock a DEK before opening `MemoryStore` / `HiveStore`.
   - **In-process** KMS plugins or automatic DEK rotation **inside** tapps-brain — **deferred** unless a future epic specifies contracts and test environments.

Revisit with a **new** story if a **product** commitment requires a **reference architecture** document for one cloud (would live under `docs/guides/` or vendor pack, not implied by core).

## Consequences

- **No** new mandatory dependencies or MCP tools.
- **CI** may continue to **skip** `requires_encryption` tests where native SQLCipher is absent; operators with encryption enabled should run those tests locally or in an image with SQLCipher (see guide **Install** section).

## References

- `sqlcipher.md` *(guide removed — SQLite retired in ADR-007)* — install, keys, CLI, backup checklist, enterprise note.
- [`EPIC-043.md`](../epics/EPIC-043.md) — STORY-043.6 baseline.
- [`EPIC-051.md`](../epics/EPIC-051.md) — STORY-051.5.
