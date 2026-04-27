# Release Checklist — tapps-brain

Distribution channel for `tapps-brain` (TAP-992).  Default path is automated:
push a `vX.Y.Z` tag; GitHub Actions builds wheel + sdist from the tag and
attaches them to a GitHub Release.  Public PyPI is intentionally not the
default — see TAP-992 for the rationale.

## Quickstart — automated GitHub Release

```bash
# 1. Bump pyproject.toml version + run the version-consistency sweep below
# 2. Update CHANGELOG.md with a `## X.Y.Z` heading and notes
# 3. Commit, push to main, wait for CI to go green
# 4. Tag and push:
git tag vX.Y.Z && git push origin vX.Y.Z
```

The [`.github/workflows/release.yml`](../.github/workflows/release.yml)
workflow then:

1. Checks out the **tag** (never `main` — see [feedback memory on this](../#release-build-from-tag-rule)).
2. Runs `scripts/release-ready.sh` with `SKIP_FULL_PYTEST=1` (the CI matrix
   has already exercised tests on this commit).
3. `uv build` wheel + sdist; smoke-installs the wheel into a clean venv and
   asserts `tapps_brain.__version__ == X.Y.Z`.
4. Creates the GitHub Release at `vX.Y.Z`, attaches both artifacts, pulls
   release notes from the matching `## X.Y.Z` block in `CHANGELOG.md`.

Consumers (AgentForge, TappsMCP, NLTlabsPE) install via:

```toml
# pyproject.toml of a consumer — replace the vendored wheel with one of:
tapps-brain = { url = "https://github.com/wtthornton/tapps-brain/releases/download/vX.Y.Z/tapps_brain-X.Y.Z-py3-none-any.whl" }
# or:
tapps-brain = { git = "https://github.com/wtthornton/tapps-brain.git", tag = "vX.Y.Z" }
```

Both forms are hash-pinnable in `uv.lock`; neither requires a `vendor/`
directory or out-of-band wheel hand-off.

## Pre-tag checklist

**Recommended single gate (same checks as CI `release-ready` job, full pytest locally):**

```bash
bash scripts/release-ready.sh
```

On Windows, run the script from **WSL** or **Git Bash** (see `docs/planning/STATUS.md`).

That script runs, in order: OpenClaw docs consistency (`scripts/check_openclaw_docs_consistency.py`), `uv build`, wheel smoke import, version consistency tests, full pytest (unless `SKIP_FULL_PYTEST=1`), ruff + format + mypy, and `openclaw-plugin` `npm ci` / build / test.

- [ ] Release gate green: `bash scripts/release-ready.sh` (or equivalent stages below if you must run piecemeal)
- [ ] All tests pass: `pytest tests/ -v --tb=short -m "not benchmark" --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95`
- [ ] Lint clean: `ruff check src/ tests/ && ruff format --check src/ tests/`
- [ ] Type check clean: `mypy --strict src/tapps_brain/`
- [ ] OpenClaw docs consistency: `python scripts/check_openclaw_docs_consistency.py` (also inside release gate)
- [ ] Version bump in `pyproject.toml` (`version = "X.Y.Z"`)
- [ ] Version strings consistent across:
  - `pyproject.toml`
  - `server.json` (`"version":`)
  - `openclaw-skill/SKILL.md` (YAML frontmatter `version:`)
  - `openclaw-plugin/package.json` (`"version":`) and `package-lock.json` (root)
  - `openclaw-plugin/openclaw.plugin.json` and `openclaw-skill/openclaw.plugin.json`
    (including `install.pip` lower bound `>=X.Y.Z` in the skill manifest)
  - `openclaw-plugin/src/index.ts` (`ContextEngineInfo.version`)
  - Run `pytest tests/unit/test_version_consistency.py -v` to verify
- [ ] CHANGELOG.md updated with release notes — heading must be `## X.Y.Z`
      so `release.yml` can extract the section
- [ ] All changes committed and pushed to `main`

## Manual fallback — local build only

If the GitHub Actions workflow is unavailable (network outage, secret
rotation, etc.) the wheel can still be built locally and attached to a
GitHub Release by hand.  Public PyPI publishing is intentionally not part
of this flow — see TAP-992 for context.

### Build

```bash
# Clean previous builds
rm -rf dist/

# Build wheel and sdist
uv build

# Verify artifacts exist
ls dist/
# Expected: tapps_brain-X.Y.Z.tar.gz  tapps_brain-X.Y.Z-py3-none-any.whl
```

### Verify Install

```bash
# Create a clean virtual environment
python3 -m venv /tmp/tapps-publish-test
source /tmp/tapps-publish-test/bin/activate

# Install from wheel (core only)
pip install dist/tapps_brain-*.whl

# Test core import
python -c "import tapps_brain; print(tapps_brain.__version__)"

# Install with CLI extra and verify entry point
pip install "dist/tapps_brain-*.whl[cli]"
tapps-brain --version

# Install with MCP extra and verify entry point
pip install "dist/tapps_brain-*.whl[mcp]"
tapps-brain-mcp --help
tapps-brain-mcp --version

# Cleanup
deactivate
rm -rf /tmp/tapps-publish-test
```

### Attach to a GitHub Release manually

```bash
gh release create vX.Y.Z \
  --title vX.Y.Z \
  --notes-file <(awk '/^## X.Y.Z/{f=1;next} f && /^## /{exit} f' CHANGELOG.md) \
  --verify-tag \
  dist/tapps_brain-X.Y.Z-py3-none-any.whl \
  dist/tapps_brain-X.Y.Z.tar.gz
```

### Optional — publish to public PyPI

Out of scope per TAP-992 (open-sourcing decision).  Re-enable by adding a
`pypi-publish` job to `.github/workflows/release.yml` that uses
`pypa/gh-action-pypi-publish` with PyPI Trusted Publisher (OIDC).  Manual
fallback uses `twine`:

```bash
uv tool install twine
twine upload --repository testpypi dist/*   # Test PyPI smoke first
twine upload dist/*                         # production PyPI
```

## Notes

- The package uses **hatchling** as its build backend
- Entry points: `tapps-brain` (CLI) and `tapps-brain-mcp` (MCP server)
- Optional extras: `cli`, `mcp`, `vector`, `reranker`, `otel`, `all`, `dev`
- Python requirement: `>=3.12`
