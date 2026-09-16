# TAP-7717: recall evidence for PR #286 (sentence-transformers ceiling `<6` -> `<7`)

PR #286 (dependabot, opened 2026-09-01) widens the `sentence-transformers` requirement in
`pyproject.toml` from `>=5.4.0,<6` to `>=5.4.0,<7`. This document is the measurement the
orchestrator uses to decide the disposition of that PR. It does not merge, close, or comment
on the PR, and it does not write to Linear.

## Starting state (verified at `4abbddcf`, 2026-09-16)

- `pyproject.toml:34` — `"sentence-transformers>=5.4.0,<6"`
- `uv.lock:1884-1885` — `name = "sentence-transformers"` / `version = "5.4.1"`
- PR #286 head `dependabot/pip/sentence-transformers-gte-5.4.0-and-lt-7`, `OPEN`, `BEHIND`,
  last updated 2026-09-10T00:03:23Z, author `app/dependabot`.
- `gh pr diff 286 --name-only` shows the PR touches **only** `pyproject.toml` — it does not
  carry a regenerated `uv.lock`.

## Branch determination: does the lock move?

Changed `pyproject.toml:34` to `"sentence-transformers>=5.4.0,<7"` (the exact PR #286 diff) and
ran `uv lock` (the repo's standard lockfile regeneration step) from this worktree.

Before (`uv.lock:1884-1886`):
```
[[package]]
name = "sentence-transformers"
version = "5.4.1"
source = { registry = "https://pypi.org/simple" }
```

After (`uv.lock:1884-1886`, unchanged):
```
[[package]]
name = "sentence-transformers"
version = "5.4.1"
source = { registry = "https://pypi.org/simple" }
```

Full `diff uv.lock.before uv.lock` after the pin bump + `uv lock`:
```
2043,2044c2043,2044
<     { name = "mcp", marker = "extra == 'http'", specifier = ">=1.25.0,<2" },
<     { name = "mcp", marker = "extra == 'mcp'", specifier = ">=1.25.0,<2" },
---
>     { name = "mcp", marker = "extra == 'http'", specifier = ">=1.25.0,<3" },
>     { name = "mcp", marker = "extra == 'mcp'", specifier = ">=1.25.0,<3" },
2052c2052
<     { name = "sentence-transformers", specifier = ">=5.4.0,<6" },
---
>     { name = "sentence-transformers", specifier = ">=5.4.0,<7" },
2066c2066
<     { name = "mcp", specifier = ">=1.25.0,<2" },
---
>     { name = "mcp", specifier = ">=1.25.0,<3" },
```

The `mcp` specifier lines are pre-existing drift unrelated to this PR: `pyproject.toml`'s `mcp`
pin was already updated to `<3` by commit `4abbddcf` (merged before this worktree was created),
but `uv.lock` had not been regenerated since. Running `uv lock` picked up both the stale `mcp`
constraint text and the `sentence-transformers` ceiling bump. Only the `[package.metadata]`
*specifier strings* change for `sentence-transformers` — the resolved `version = "5.4.1"` under
`[[package]]` at line 1885 is byte-identical before and after.

**Confirmed: `uv lock -> 5.4.1` on both sides. The lock does not move.** This is deterministic,
not a fluke of `uv`'s minimal-update policy dodging a real newer resolution: forcing
`uv lock --upgrade-package sentence-transformers` on this same tree resolves cleanly to
`6.0.1` (`Updated sentence-transformers v5.4.1 -> v6.0.1`), so 6.x **is** installable under the
widened ceiling — dependabot (and a plain `uv lock` after the pin bump) simply doesn't pick it
because nothing forces the upgrade. `uv.lock` was reset to the plain-`uv lock` result (5.4.1)
before proceeding, since that reflects what actually ships if PR #286 merges as-is and someone
runs `uv lock`/`uv sync` afterward — not the forced-upgrade probe.

This is **Branch B**: no installed version moved, so recall cannot have changed as a
*consequence of this PR*.

## Required research: sentence-transformers 6.x release notes

Looked up the 6.0.0 release notes (GitHub release, huggingface/sentence-transformers, and the
text quoted in PR #286's own dependabot changelog block). Relevant points:

- v6.0.0 adds `MultiVectorEncoder` for ColBERT-style late-interaction / multi-vector retrieval —
  a new model class, not a change to the existing `SentenceTransformer` class this repo uses
  (`src/tapps_brain/embeddings.py` imports `from sentence_transformers import SentenceTransformer`
  only).
- v6.0.0 raises the dependency floor to `transformers` v5 and fixes "a class of silent scoring
  bugs caused by half precision" (float32 scoring fix) — described in the release notes as a
  bug *fix* to CrossEncoder/reranker score computation under fp16, not a change to
  `SentenceTransformer.encode()`'s default pooling or normalization for the dense embedding path
  this repo uses.
- v6.0.0 is flagged `[!WARNING] breaking changes` in general, with a migration guide; the
  specific breaking-change list (per the release notes) is scoped to APIs this repo does not
  call (`MultiVectorEncoder`, training-loss extensions, ONNX/OpenVINO backends).
- No change to `SentenceTransformer`'s default pooling strategy or default model revision
  resolution is mentioned in the 6.0.0 notes; this repo pins both explicitly anyway
  (`model_name="BAAI/bge-small-en-v1.5"`, `revision="5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"`
  in `src/tapps_brain/embeddings.py`), so even a v6.x install would load the same weights at the
  same revision.

Given the lock doesn't move under Branch B, none of the above is currently load-bearing for this
repo — it is recorded here because the acceptance criteria require citing 6.x's changes before
making any recall claim, and because it is directly relevant if a future PR *forces* the 6.x
resolution (see Recommendation below).

## b1 — fixed corpus, same query, current pin vs. bumped resolution

**NOT-APPLICABLE.** Branch B: the resolved `sentence-transformers` version is `5.4.1` both
before and after the `pyproject.toml` pin change (see diff above). There is only one environment
to seed — there is no "current pin" vs. "bumped resolution" pair to compare, and staging one
environment's output as two would be a fabricated measurement per this lane's own instructions.

**Positive control (still required, both branches):** built the comparison harness (see b3) and
ran it against the single 5.4.1 environment. The seeded corpus is non-empty and the query
returns a non-zero id count — see the b3 output below (`corpus_size=10`,
`baseline_ids_in_order=[5, 4, 2, 1, 3, 8, 9, 7, 6, 10]`, 10 ids).

## b2 — returned ids and ordering identical or every difference named

**NOT-APPLICABLE**, for the same reason as b1: there is one environment, not two, so there is no
before/after id-ordering pair to compare.

## b3 — the comparison harness is shown able to see a change (REQUIRED under both branches)

Stood up a throwaway, uniquely-named standalone pgvector container (`docker run`, not
`docker compose`):

```
$ docker run -d --name pgvector-tap7717-3459636 \
    -e POSTGRES_PASSWORD=tapps -e POSTGRES_USER=tapps -e POSTGRES_DB=tapps_test \
    -p 0:5432 pgvector/pgvector:pg17
3c54cd5e4c5c1696f5d03b0f333e04b3cf71b8675a315ff9e0807fcababb10ea
$ docker ps --filter name=pgvector-tap7717-3459636 --format '{{.Names}}\t{{.Ports}}'
pgvector-tap7717-3459636       0.0.0.0:32768->5432/tcp, [::]:32768->5432/tcp
$ docker exec pgvector-tap7717-3459636 psql -U tapps -d tapps_test -c "CREATE EXTENSION IF NOT EXISTS vector;"
CREATE EXTENSION
```

This container never touched `TAPPS_BRAIN_DATABASE_URL`, port 8080, or any container named
`tapps-brain-*`; it was `docker rm -f`'d immediately after the run below (verified: no
`*tap7717*` container remains).

Harness (`docs/evidence/../../` — kept outside `src/` per the file partition; the script itself
lives in the session scratchpad, not committed, since it is not part of the shipped package):
a 10-document corpus embedded with the installed `sentence-transformers==5.4.1` /
`BAAI/bge-small-en-v1.5` (the same model+revision `src/tapps_brain/embeddings.py` pins), inserted
into a `VECTOR(384)` column, queried by cosine distance (`<=>`) for
`"How does the system rank and combine search results?"`, then one document (id 5 — originally
about "reciprocal rank fusion", topically closest to the query) rewritten to an unrelated topic
("Bananas are a good source of potassium...") and the same query re-run against the re-seeded
corpus.

Literal output:
```
st_version=5.4.1
corpus_size=10
query_text='How does the system rank and combine search results?'
baseline_ids_in_order=[5, 4, 2, 1, 3, 8, 9, 7, 6, 10]
perturbed_ids_in_order=[4, 2, 1, 3, 8, 9, 7, 6, 5, 10]
HARNESS_RESULT=DIFFERENT_AFTER_PERTURBATION -> OK
```

10 ids compared before vs. after perturbation. Doc 5 moves from rank 1 to rank 9 (all other 9
ids retain relative order among themselves). The harness reports a difference, not "identical" —
**b3 = PASS**, on the only branch this PR exercises (5.4.1 -> 5.4.1, so the harness ran once
against one environment, which is exactly what b3 requires: proof the comparison mechanism can
detect change, independent of whether this specific PR produces any).

Container teardown:
```
$ docker rm -f pgvector-tap7717-3459636
pgvector-tap7717-3459636
$ docker ps -a --format '{{.Names}}' | grep -i tap7717
container removed, none remaining
```

## Recommendation (b4 is the orchestrator's box, not mine)

The data supports: **PR #286 changes nothing today.** Bumping the ceiling from `<6` to `<7`
does not move the resolved `sentence-transformers` version away from `5.4.1` under either
dependabot's own PR (which only touches `pyproject.toml`) or a subsequent `uv lock` run in this
repo — `uv`'s minimal-update resolution keeps the existing pin unless something forces an
upgrade. There is therefore no recall behavior to regress, today, from merging this PR alone.

The ceiling bump does make it *possible* for a later `uv lock --upgrade-package
sentence-transformers` (or a routine "upgrade everything" lockfile refresh) to jump straight to
`6.0.1`, which this session confirmed resolves cleanly. Per the 6.0.0 release notes, that
version doesn't appear to change this repo's embedding path (pinned model + revision, no use of
`MultiVectorEncoder`), but that is a claim about the *next* lockfile regen, not about this PR —
it was not measured here because Branch B means there is no bumped-resolution environment to
seed in this PR's actual diff. If the orchestrator merges #286, the safer follow-up is a
scheduled or manual `uv lock --upgrade-package sentence-transformers` in its own PR, which
*would* move the lock and should re-run this same b1/b2/b3 harness (Branch A) before merging.
