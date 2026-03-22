# PyPI Publish Checklist — tapps-brain

Manual steps to publish a new release of `tapps-brain` to PyPI.

## Pre-flight

- [ ] All tests pass: `pytest tests/ -v --tb=short --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95`
- [ ] Lint clean: `ruff check src/ tests/ && ruff format --check src/ tests/`
- [ ] Type check clean: `mypy --strict src/tapps_brain/`
- [ ] Version bump in `pyproject.toml` (`version = "X.Y.Z"`)
- [ ] Version strings consistent across:
  - `pyproject.toml`
  - `openclaw-skill/SKILL.md` (YAML frontmatter `version:`)
  - `openclaw-plugin/package.json` (`"version":`)
  - `openclaw-skill/openclaw.plugin.json` (`"version":`)
  - Run `pytest tests/unit/test_version_consistency.py -v` to verify
- [ ] CHANGELOG.md updated with release notes (if maintained)
- [ ] All changes committed and pushed to `main`

## Build

```bash
# Clean previous builds
rm -rf dist/

# Build wheel and sdist
uv build

# Verify artifacts exist
ls dist/
# Expected: tapps_brain-X.Y.Z.tar.gz  tapps_brain-X.Y.Z-py3-none-any.whl
```

## Verify Install

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

# Cleanup
deactivate
rm -rf /tmp/tapps-publish-test
```

## Publish to Test PyPI (recommended first)

```bash
# Install twine if not already available
uv tool install twine

# Upload to Test PyPI
twine upload --repository testpypi dist/*

# Verify install from Test PyPI
pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  tapps-brain
```

## Publish to PyPI

```bash
# Upload to production PyPI
twine upload dist/*

# Verify install from PyPI
pip install tapps-brain
tapps-brain --version
```

## Post-publish

- [ ] Verify package page at https://pypi.org/project/tapps-brain/
- [ ] Tag the release: `git tag vX.Y.Z && git push origin vX.Y.Z`
- [ ] Create GitHub release from the tag (attach wheel and sdist)
- [ ] Verify install from PyPI in a clean environment

## Authentication

PyPI uploads require authentication. Options:

1. **API token** (recommended): Generate at https://pypi.org/manage/account/token/
   - Configure in `~/.pypirc` or pass via `TWINE_USERNAME=__token__ TWINE_PASSWORD=<token>`
2. **Trusted publisher** (CI): Configure in PyPI project settings to allow GitHub Actions OIDC

## Notes

- The package uses **hatchling** as its build backend
- Entry points: `tapps-brain` (CLI) and `tapps-brain-mcp` (MCP server)
- Optional extras: `cli`, `mcp`, `vector`, `reranker`, `otel`, `all`, `dev`
- Python requirement: `>=3.12`
