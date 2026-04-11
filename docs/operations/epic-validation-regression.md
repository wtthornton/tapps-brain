---
title: "Epic Validation — Regression Runbook"
created: 2026-04-11
category: operations
---

# Epic Validation — Regression Runbook

This runbook verifies that the **Epic Validation** CI job (`.github/workflows/epic-validation.yml`)
correctly blocks merge when epic frontmatter is broken.

## What is validated?

The script `scripts/validate_epics.py` checks each of `EPIC-059.md`–`EPIC-063.md` for:

| Field | Rule |
|-------|------|
| `id` | Present; matches `EPIC-NNN` pattern; must equal filename stem |
| `title` | Present; non-empty |
| `status` | One of `planned`, `in-progress`, `complete` |
| `priority` | One of `critical`, `high`, `medium`, `low` |
| `created` | Present; `YYYY-MM-DD` format |
| `tags`, `depends_on`, `blocks` | Present (may be empty lists `[]`) |

## Run locally

```bash
# Validate all five v3 epics:
python3 scripts/validate_epics.py \
  docs/planning/epics/EPIC-059.md \
  docs/planning/epics/EPIC-060.md \
  docs/planning/epics/EPIC-061.md \
  docs/planning/epics/EPIC-062.md \
  docs/planning/epics/EPIC-063.md

# Validate a single file:
python3 scripts/validate_epics.py docs/planning/epics/EPIC-062.md

# Validate every epic in the directory:
python3 scripts/validate_epics.py docs/planning/epics/
```

Exit code `0` = all valid. Exit code `1` = one or more failures (CI will turn red).

## Regression gate verification (maintainer step)

Follow these steps to confirm the gate is working end-to-end:

1. **Create a test branch:**
   ```bash
   git checkout -b test/epic-validation-gate
   ```

2. **Break a frontmatter field** — e.g. change `status: planned` → `status: invalid-value`
   in `docs/planning/epics/EPIC-062.md`.

3. **Verify local failure:**
   ```bash
   python3 scripts/validate_epics.py docs/planning/epics/EPIC-062.md
   # Expected output:
   # FAIL  docs/planning/epics/EPIC-062.md
   #       'status' must be one of ['complete', 'in-progress', 'planned'], got: 'invalid-value'
   # validate_epics: 1/1 file(s) failed validation
   # Exit code: 1
   ```

4. **Open a PR** from `test/epic-validation-gate` → `main`. The **Epic Validation** CI
   job should turn **red** and block merge.

5. **Revert the change**, push to the same branch, and confirm the job turns **green**.

6. **Close (do not merge)** the test PR and delete the branch.

## When does the CI job run?

The `epic-validation.yml` workflow runs on `pull_request` and `push` to `main`/`master`
whenever any of `docs/planning/epics/EPIC-059*.md`–`EPIC-063*.md` is in the diff.
It is skipped entirely if none of those files changed.

## Adding new epics to the gate

To extend coverage to additional epic files, update two places:

1. **`scripts/validate_epics.py`** — the default glob in `main()` (or pass paths explicitly).
2. **`.github/workflows/epic-validation.yml`** — the `paths:` filter and the `python3 scripts/validate_epics.py ...` command.
