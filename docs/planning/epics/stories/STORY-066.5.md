# Story 66.5 -- Version consistency unblock for openclaw-skill

<!-- docsmcp:start:user-story -->

> **As a** release manager, **I want** openclaw-skill SKILL.md to declare the same version as pyproject.toml, **so that** the test_all_versions_match release gate stops blocking the unit suite and the OpenClaw skill ships with the correct version metadata

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 1 | **Size:** S

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the version consistency gate that has been failing since well before ADR-007 stops blocking releases. test_all_versions_match asserts that openclaw-skill/SKILL.md and pyproject.toml report the same version; SKILL.md is currently 3.2.0 and pyproject.toml is 3.3.0 (will become 3.4.0 when EPIC-066 ships).

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

Bump openclaw-skill/SKILL.md to whatever the target release version is, audit any other version-bearing files (server.json, llms.txt, package.json in openclaw-plugin) for the same drift, and confirm scripts/check_openclaw_docs_consistency.py still passes. Add the SKILL.md version to the release gate checklist so future bumps do not regress.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `openclaw-skill/SKILL.md`
- `openclaw-plugin/package.json`
- `server.json`
- `llms.txt`
- `scripts/publish-checklist.md`
- `tests/unit/test_version_consistency.py`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [x] Bump openclaw-skill/SKILL.md to match pyproject.toml version (`openclaw-skill/SKILL.md`)
- [x] Audit openclaw-plugin/package.json for the same version (`openclaw-plugin/package.json`)
- [x] Audit server.json and llms.txt for any embedded version strings (`server.json`)
- [x] Verify scripts/check_openclaw_docs_consistency.py passes (`scripts/check_openclaw_docs_consistency.py`)
- [x] Add SKILL.md version bump to scripts/publish-checklist.md (`scripts/publish-checklist.md`)
- [x] Verify test_all_versions_match passes (`tests/unit/test_version_consistency.py`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [x] openclaw-skill/SKILL.md version equals pyproject.toml version
- [x] openclaw-plugin/package.json version equals pyproject.toml version
- [x] scripts/check_openclaw_docs_consistency.py passes
- [x] test_all_versions_match passes
- [x] scripts/publish-checklist.md mentions SKILL.md as a version-bumped file

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

- [x] All tasks completed
- [x] Version consistency unblock for openclaw-skill code reviewed and approved
- [x] Tests passing (unit + integration)
- [x] Documentation updated
- [x] No regressions introduced

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_openclawskillskillmd_version_equals_pyprojecttoml_version` -- openclaw-skill/SKILL.md version equals pyproject.toml version
2. `test_ac2_openclawpluginpackagejson_version_equals_pyprojecttoml_version` -- openclaw-plugin/package.json version equals pyproject.toml version
3. `test_ac3_scriptscheckopenclawdocsconsistencypy_passes` -- scripts/check_openclaw_docs_consistency.py passes
4. `test_ac4_testallversionsmatch_passes` -- test_all_versions_match passes
5. `test_ac5_scriptspublishchecklistmd_mentions_skillmd_as_versionbumped_file` -- scripts/publish-checklist.md mentions SKILL.md as a version-bumped file

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- This is a one-line bump but the dependency on the actual release version means it must land near the end of the epic right before the 3.4.0 tag. Coordinate with the maintainer doing the version bump.

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- List stories or external dependencies that must complete first...

<!-- docsmcp:end:dependencies -->

<!-- docsmcp:start:invest -->
## INVEST Checklist

- [x] **I**ndependent -- Can be developed and delivered independently
- [ ] **N**egotiable -- Details can be refined during implementation
- [x] **V**aluable -- Delivers value to a user or the system
- [x] **E**stimable -- Team can estimate the effort
- [x] **S**mall -- Completable within one sprint/iteration
- [x] **T**estable -- Has clear criteria to verify completion

<!-- docsmcp:end:invest -->
