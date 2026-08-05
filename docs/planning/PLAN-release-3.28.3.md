# Release plan — 3.28.3 (`/v1/remember` acknowledged write loss)

**Status:** proposed, 2026-08-04
**Ships:** TAP-5615, TAP-5616, TAP-5617 (epic TAP-5614)
**Version:** `3.28.2` → `3.28.3` — patch. No schema migration (max private
migration stays 029), no removed field, no changed request shape. The response
gains `coalesced` / `persisted` / `coalesced_into` / `invalidated`; all
additive, and the one *changed* behaviour (a coalesced write no longer reports
`"saved"`) replaces a false claim on a path that is unreachable after TAP-5615.

---

## 0. Preconditions

1. **The working tree is shared with another session.** `docs/GITHUB_SETUP_GUIDE.md`
   is modified by someone else. Stage explicitly by path — never `git add -A`.
2. **Branch before committing.** Current branch is `main`; the release commit
   should land via a branch + PR, or at minimum a deliberate direct push.
3. `origin/main` is currently in sync — no unpushed commits.

Files this release touches (mine):

```
src/tapps_brain/store.py
src/tapps_brain/services/memory_service.py
src/tapps_brain/http_adapter.py
tests/unit/test_save_write_loss.py        (new)
tests/unit/test_save_many.py
tests/unit/test_http_adapter.py
tests/unit/test_bulk_operations.py
docs/guides/agentforge-integration.md
docs/planning/PLAN-remember-acknowledged-write-loss.md   (new)
docs/planning/PLAN-release-3.28.3.md                     (new, this file)
```

---

## 1. Known trap — the OpenAPI snapshot is already red

`tests/unit/test_openapi_contract.py::test_runtime_spec_matches_checked_in_snapshot`
**fails on the current tree.** The `/v1/remember` docstring now documents the
response statuses, FastAPI puts docstrings into the spec description, and
`docs/contracts/openapi.json` still holds the old text.

This is the exact failure mode that bit 3.28.2 — commit `35f155f` had to follow
`f446bde` to repair three release-artifact gates the bump left red. Do the
snapshot regeneration **after** the version bump in §2 (the script writes a
version-pinned filename, so running it at 3.28.2 produces the wrong artifact),
and before the release gate in §4.

---

## 2. Version sweep

`tests/unit/test_version_consistency.py` pins five files to `pyproject.toml`.
All five must move together:

1. `pyproject.toml` — `version = "3.28.3"`
2. `server.json` — `"version": "3.28.3"`
3. `.claude/skills/tapps-brain/SKILL.md` — `version: "3.28.3"` frontmatter
4. `src/tapps_brain/_assets/tapps-brain-skill.md` — `version: "3.28.3"` frontmatter
5. `docker/.env.example` — `BRAIN_VERSION=3.28.3`

**Do not bump only the number in items 3 and 4.** The checklist requires
reviewing the skill body against this release's changes, and this release
changes the agent-facing save contract. Both skill files describe
`brain_remember`; if either documents the response envelope, add the
`coalesced` / `persisted` / `invalidated` fields there. If neither mentions the
envelope, note that and move on — do not invent a section.

Verify:

```bash
uv run pytest tests/unit/test_version_consistency.py -v
```

---

## 3. Regenerate release artifacts

```bash
uv run python scripts/snapshot_openapi.py     # writes openapi.json + openapi-3.28.3.json
```

Then update the version line in both `llms.txt` and `llms-full.txt`
(`- Version: 3.28.3`).

Verify all three artifact gates go green:

```bash
uv run pytest tests/unit/test_openapi_contract.py tests/unit/test_release_artifacts.py -q
```

Expect `docs/contracts/openapi-3.28.3.json` to be a new ~2.4k-line file — that
is normal, one per release.

---

## 4. CHANGELOG

Add above `## [3.28.2]`, matching the existing bracketed style (the
`release.yml` extractor accepts `^## (\[)?3\.28\.3`, so brackets are safe):

```markdown
## [3.28.3] — 2026-08-04

Patch release: `/v1/remember` acknowledged writes it did not persist.

### Fixed

- **Save-path dedup discarded writes under distinct keys** ([TAP-5615](...)) — …
- **Reviving a superseded key returned a spurious 400** ([TAP-5616](...)) — …

### Changed

- **`/v1/remember` response contract** ([TAP-5617](...)) — a write folded onto
  another row now returns `status: "coalesced"` with `persisted: false` and
  `coalesced_into`, never `"saved"` with a foreign key. Saves that close a
  neighbour's validity interval list those keys in `invalidated`.
```

Keep the entries in the house style: what broke, the mechanism, and why it was
invisible — the 3.28.2 entries are the model.

---

## 5. Release gate

**The gate needs a live Postgres.** `scripts/release-ready.sh` enforces
`--cov-fail-under=80` and runs `tests/compat/` under
`TAPPS_BRAIN_TESTS_STRICT=1`, which fails outright without
`TAPPS_BRAIN_DATABASE_URL`. Without a DB the suite skips 475 tests and lands at
**79.23 %** — under the 80 floor. The gate will fail; that is the gate working,
not a regression.

**Do not hard-code port 5432.** On this host 5432 is held by an unrelated
project's container (`nlt-research-postgres`), and *neither* brain DB container
publishes a host port — `tapps-brain-db` (compose project `tapps-brain`, the
deployed stack) and `tapps-brain-prod-db` (project `agentforge-prod`) both bind
container-side only. A literal `localhost:5432` therefore aims the suite at
whatever happens to own that port, which is the exact failure the Makefile warns
about ("a hard-coded 5432 silently targets the wrong container"). Pick a free
port and thread it through `TAPPS_DEV_PORT`, which both `brain-up` and
`TAPPS_DEV_DSN` already honour:

```bash
export TAPPS_DEV_PORT=55432          # any free port; 5432 is taken on this host
make brain-up                        # project tapps-brain-dev → tapps-brain-dev-db
make brain-migrate                   # prints the DSN it used — copy it, don't retype it

DSN=postgres://tapps:tapps@localhost:$TAPPS_DEV_PORT/tapps_brain_dev
export TAPPS_BRAIN_DATABASE_URL=$DSN
export TAPPS_TEST_POSTGRES_DSN=$DSN
export TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1
export TAPPS_BRAIN_TESTS_STRICT=1

bash scripts/release-ready.sh
```

Confirm you are on the dev DB before running the suite — expect `29`:

```bash
docker exec tapps-brain-dev-db psql -U tapps -d tapps_brain_dev \
  -tAc "select max(version) from private_schema_version;"
```

This is the dev database (`tapps-brain-dev` compose project, container
`tapps-brain-dev-db`), deliberately separate from the deployed stack's
`tapps-brain` project — running the suite against it does not touch live memory.

Already verified without a DB: 5,423 passed / 0 failed, `ruff check` clean,
`ruff format --check` clean, `mypy --strict` clean. The DB-backed run is what
remains unproven locally.

---

## 6. Commit, push, tag

```bash
git switch -c release/3.28.3
git add <the explicit file list from §0, plus the §2/§3/§4 artifacts>
git commit   # release(3.28.3): /v1/remember no longer acknowledges lost writes
git push -u origin release/3.28.3
```

Merge to `main` once CI is green, then:

```bash
git switch main && git pull
git tag v3.28.3 && git push origin v3.28.3
```

`.github/workflows/release.yml` checks out the **tag** (never `main`), runs
`release-ready.sh` with `SKIP_FULL_PYTEST=1`, builds wheel + sdist, smoke-installs
and asserts `__version__ == 3.28.3`, and publishes the GitHub Release with the
CHANGELOG section as notes.

**If you build a wheel by hand instead**, build it from a worktree at the tag,
not from `main` — `release-ready.sh` does `rm -rf dist/ && uv build`, which will
happily stamp a `3.28.3`-named wheel with whatever `main` currently holds.
SHA-verify against a fresh worktree build.

---

## 7. Deploy the local stack

**One host step I cannot do:** `docker/.env` is deny-listed for the agent.
`make check-brain-env` hard-fails on the drift, so this must happen first:

```bash
sed -i 's/^BRAIN_VERSION=.*/BRAIN_VERSION=3.28.3/' docker/.env
```

Then:

```bash
make dev-deploy          # rebuilds wheel + http image, recreates tapps-brain-http, runs brain-smoke-live
```

No `MIGRATE=1` — this release touches no SQL under `src/tapps_brain/migrations/`,
and `scripts/dev-deploy.sh` detects that itself. Compose pins
`docker-tapps-brain-http:${BRAIN_VERSION}`, so the §7 sed is what makes the new
image tag roll rather than silently rebuilding the `3.28.2` tag with 3.28.3 code.

---

## 8. Post-deploy verification

The smoke suite in `dev-deploy` proves the container is up. It does **not**
prove this bug is fixed. Run the reporter's own probe against the live instance:

```bash
# five distinct keys, one value — all five must persist
for i in 0 1 2 3 4; do
  curl -sS -X POST http://127.0.0.1:8080/v1/remember \
    -H "Authorization: Bearer $TAPPS_BRAIN_AUTH_TOKEN" \
    -H 'X-Project-Id: smoke-write-loss' -H 'Content-Type: application/json' \
    -d "{\"key\":\"diag-echo-$i\",\"value\":\"echo-probe\",\"tier\":\"context\"}"
  echo
done
for i in 0 1 2 3 4; do
  curl -sS -X POST http://127.0.0.1:8080/v1/forget \
    -H "Authorization: Bearer $TAPPS_BRAIN_AUTH_TOKEN" \
    -H 'X-Project-Id: smoke-write-loss' -H 'Content-Type: application/json' \
    -d "{\"key\":\"diag-echo-$i\"}"
  echo
done
```

Pass = five `"status":"saved"` responses each echoing their own key, then five
`"forgotten":true`. Use the `smoke-` project prefix so
`make purge-test-tenants` can reclaim the rows.

Also confirm `/info` (or `brain_bridge_health`) reports `brain_version: 3.28.3`.

---

## 9. Close out

1. Move TAP-5615, TAP-5616, TAP-5617 to Done; TAP-5614 to Done. TAP-5629
   (write-only bloom filter) stays open — it is deliberately not in this release.
2. Post the Linear release update via the `linear-release-update` skill
   (`tapps_release_update` → validate → `save_document` → snapshot invalidate).
3. **Reply to nlt-ideas-scout** at
   `docs/cross-project/PROMPT-brain-remember-write-loss.RESPONSE.md`, now that
   there is a version to name. Cover: symptoms confirmed; concurrency and clock
   resolution ruled out; the two root causes with `file:line`; landed in 3.28.3;
   their 7-of-24 400s never persisted at all and are re-writable on the next
   poll tick; no consumer-side change wanted. This is a cross-project write —
   confirm before sending.
4. **AgentForge needs nothing.** It dropped the vendored wheel at TAP-995 and has
   been HTTP-only since: no `tapps-brain` dependency, no `import tapps_brain`, no
   wheel on disk. It holds only `TAPPS_BRAIN_HTTP_URL` + `TAPPS_BRAIN_AUTH_TOKEN`
   and adopts whatever version the sidecar reports (`/v1/tools/list`
   `X-Brain-Version` → `/healthz brain_version` → `/health.version`) on next boot.
   Redeploying the brain compose *is* the handover.

   An earlier revision of this plan said to hand AgentForge a `.whl`. That step
   was dead as of TAP-995 and generated a false ask to the consumer, who had to
   correct it. Do not reinstate it.

   The consumer that *does* still install the package is **tapps-mcp**, which pins
   `tapps-brain = { git = ..., tag = "vX.Y.Z" }` in its `pyproject.toml`. That pin
   does not follow a new release — check it if tapps-mcp needs the new version.

---

## Sequence summary

| # | Step | Blocking on |
|---|---|---|
| 1 | Branch, confirm the other session's file stays unstaged | — |
| 2 | Version sweep, 5 files + skill body review | — |
| 3 | `snapshot_openapi.py`, `llms*.txt` version lines | §2 (version-pinned filename) |
| 4 | CHANGELOG `## [3.28.3]` | — |
| 5 | `brain-up` + `brain-migrate` + `release-ready.sh` | §2–§4 |
| 6 | Commit, push, CI green, merge, tag `v3.28.3` | §5 |
| 7 | `docker/.env` BRAIN_VERSION bump (**host step**), `make dev-deploy` | §6 |
| 8 | Live five-key probe + version check | §7 |
| 9 | Linear close-out, consumer reply, AgentForge wheel | §8 |
