# ClawHub Submission Guide

How to submit `tapps-brain-memory` to the ClawHub skill directory.

## Pre-Submission Checklist

Before submitting, verify the skill directory is complete and valid:

- [ ] `openclaw-skill/SKILL.md` exists with valid YAML frontmatter
- [ ] `openclaw-skill/openclaw.plugin.json` exists with valid JSON
- [ ] `openclaw-skill/README.md` exists for the ClawHub listing page
- [ ] Version strings match across all manifests (see below)
- [ ] `pip install tapps-brain[mcp]` installs cleanly
- [ ] `tapps-brain-mcp --help` runs without errors

### Version Consistency

All version strings must match. Verify with:

```bash
# pyproject.toml
grep '^version' pyproject.toml

# SKILL.md frontmatter
head -5 openclaw-skill/SKILL.md | grep version

# openclaw.plugin.json
python -c "import json; print(json.load(open('openclaw-skill/openclaw.plugin.json'))['version'])"

# openclaw-plugin/package.json
python -c "import json; print(json.load(open('openclaw-plugin/package.json'))['version'])"
```

An automated test (`tests/unit/test_version_consistency.py`) also validates this.

## Skill Directory Structure

ClawHub expects the following files in the skill directory:

```
openclaw-skill/
  SKILL.md               # Skill manifest (YAML frontmatter + docs)
  openclaw.plugin.json    # Plugin auto-configuration
  README.md               # ClawHub listing page content
```

### SKILL.md Schema

The YAML frontmatter in `SKILL.md` must include these required fields:

| Field          | Type     | Description                              |
|----------------|----------|------------------------------------------|
| `name`         | string   | Unique skill identifier (kebab-case)     |
| `version`      | string   | Semver version                           |
| `displayName`  | string   | Human-readable name for the listing      |
| `description`  | string   | Short description (under 200 chars)      |
| `author`       | string   | Author or organization name              |
| `license`      | string   | SPDX license identifier                  |
| `slot`         | string   | OpenClaw slot (`ContextEngine`, etc.)    |
| `install`      | string   | Install command                          |
| `homepage`     | string   | Project homepage URL                     |
| `repository`   | string   | Source repository URL                    |
| `triggers`     | string[] | Lifecycle hooks used                     |
| `capabilities` | string[] | Capability tags                          |
| `permissions`  | string[] | Required permissions                     |
| `tools`        | object[] | MCP tools exposed (name + description)   |

Optional fields: `documentation`, `keywords`, `minOpenClawVersion`.

### openclaw.plugin.json Schema

| Field          | Type   | Description                               |
|----------------|--------|-------------------------------------------|
| `name`         | string | Must match SKILL.md `name`                |
| `version`      | string | Must match SKILL.md `version`             |
| `displayName`  | string | Must match SKILL.md `displayName`         |
| `description`  | string | Short description                         |
| `slot`         | string | OpenClaw slot                             |
| `install`      | object | Install instructions (`pip`, `npm`, etc.) |
| `mcp`          | object | MCP server config (command, transport)    |
| `hooks`        | string[] | Lifecycle hooks                         |
| `capabilities` | object | Capability flags (boolean values)         |
| `settings`     | object | Default settings                          |

## Submission Process

### 1. Validate Locally

```bash
# Run the version consistency test
pytest tests/unit/test_version_consistency.py -v

# Validate SKILL.md frontmatter parses correctly
python -c "
import yaml
with open('openclaw-skill/SKILL.md') as f:
    content = f.read()
    front = content.split('---')[1]
    data = yaml.safe_load(front)
    required = ['name', 'version', 'displayName', 'description',
                'author', 'license', 'slot', 'install', 'triggers',
                'capabilities', 'permissions', 'tools']
    missing = [k for k in required if k not in data]
    assert not missing, f'Missing fields: {missing}'
    print('SKILL.md frontmatter: OK')
"

# Validate openclaw.plugin.json
python -c "
import json
with open('openclaw-skill/openclaw.plugin.json') as f:
    data = json.load(f)
    required = ['name', 'version', 'slot', 'mcp', 'hooks']
    missing = [k for k in required if k not in data]
    assert not missing, f'Missing fields: {missing}'
    print('openclaw.plugin.json: OK')
"
```

### 2. Create a Release Tag

```bash
# Ensure pyproject.toml version matches skill version
git tag v1.1.0
git push origin v1.1.0
```

### 3. Submit to ClawHub

```bash
# Option A: CLI submission (when available)
openclaw skill submit openclaw-skill/

# Option B: Manual submission
# 1. Fork the ClawHub registry repository
# 2. Add skill directory under skills/tapps-brain-memory/
# 3. Open a pull request with the skill files
# 4. ClawHub CI validates the schema and runs install tests
```

### 4. Post-Submission

After the skill is listed on ClawHub:

- Verify the listing page renders README.md correctly
- Test installation: `openclaw skill install tapps-brain-memory`
- Monitor for user issues in the repository issue tracker

## Updating an Existing Skill

To publish a new version:

1. Bump version in `pyproject.toml`, `SKILL.md`, `openclaw.plugin.json`,
   and `openclaw-plugin/package.json`
2. Run `pytest tests/unit/test_version_consistency.py -v` to verify
3. Publish the new PyPI release
4. Re-submit to ClawHub with updated files
5. Tag the release: `git tag vX.Y.Z && git push origin vX.Y.Z`

## Troubleshooting

### "Schema validation failed"

Check that all required YAML frontmatter fields are present in `SKILL.md`
and that `openclaw.plugin.json` is valid JSON. Use the validation scripts above.

### "Version mismatch"

Run the version consistency test to find which file is out of sync:

```bash
pytest tests/unit/test_version_consistency.py -v
```

### "Install test failed"

Ensure `tapps-brain[mcp]` installs cleanly in a fresh virtualenv:

```bash
python -m venv /tmp/test-install
/tmp/test-install/bin/pip install tapps-brain[mcp]
/tmp/test-install/bin/tapps-brain-mcp --help
```
