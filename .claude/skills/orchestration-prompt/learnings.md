# orchestration-prompt learnings (project-scoped)

Append one-line lessons as you generate prompts. Keep them project-scoped; never
bleed across repos. This file is created once by the scaffolder and never
overwritten on upgrade — it's yours.

<!-- Example: -->
<!-- - Validation goals need a verified-correct-negative Done-when, or the loop chases an unreachable target. (2026-06-18) -->

- Read the CI/config the loop will touch *before* drafting — for TAP-5459 that surfaced a hard blocker (`test_rls_spike.py` assumes a `tapps_runtime` role CI never creates) that would have failed the loop's first real run. A prompt written from the plan alone would have sent the runner into a diagnose cycle. (2026-08-03)
- This repo's `PreToolUse` sentinel on `save_issue` (30-min TTL, written by `docs_validate_linear_issue`) makes parallel Linear writes unsafe — agents race on a shared sentinel and a save can pair with another's validation. Bulk issue updates here are sequential-by-construction; say so explicitly or the runner defaults to a Workflow fan-out. (2026-08-03)
- Bake measured baselines into the prompt (suite counts, timings, non-default ports like `TAPPS_DEV_PORT=5442`). A cold session otherwise burns iterations rediscovering that `make brain-test` defaults to the wrong port. (2026-08-03)
