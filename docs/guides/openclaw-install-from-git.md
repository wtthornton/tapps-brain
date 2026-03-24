# Install and upgrade tapps-brain for OpenClaw from GitHub (no PyPI)

> Canonical source-of-truth for all install/upgrade paths is
> [openclaw-runbook.md](./openclaw-runbook.md). This file remains the Git-focused variant.

Use this guide when you want the **Python** package and **`tapps-brain-mcp`** installed or upgraded from the Git repository instead of PyPI. Point an OpenClaw agent or operator at this file (or at `openclaw-skill/SKILL.md`, which links the same steps in prose).

**Repository:** `https://github.com/wtthornton/tapps-brain`

## 1. Install the Python package from Git

Pick **one** of these (same environment OpenClaw uses for MCP — often the gateway’s Python or your project venv).

**Track `main` (latest):**

```bash
pip install "git+https://github.com/wtthornton/tapps-brain.git@main#egg=tapps-brain[mcp]"
```

**Pin a release tag (reproducible):**

```bash
pip install "git+https://github.com/wtthornton/tapps-brain.git@v1.3.0#egg=tapps-brain[mcp]"
```

**Editable clone (development):**

```bash
git clone https://github.com/wtthornton/tapps-brain.git
cd tapps-brain
pip install -e ".[mcp]"
```

Verify:

```bash
tapps-brain --version
tapps-brain-mcp --version
```

## 2. Install the ContextEngine plugin (TypeScript)

OpenClaw still needs the **plugin** package built from this repo (not from PyPI):

```bash
cd tapps-brain/openclaw-plugin
npm install
npm run build
openclaw plugin install .
```

## 3. OpenClaw config

Enable the plugin slot (same as the [main OpenClaw guide](./openclaw.md)):

```yaml
plugins:
  slots:
    contextEngine: tapps-brain-memory
  entries:
    tapps-brain-memory:
      enabled: true
      config:
        mcpCommand: tapps-brain-mcp
        tokenBudget: 2000
        captureRateLimit: 3
```

---

## Upgrade (Git-only)

Do these in the **same Python environment** OpenClaw uses for `tapps-brain-mcp`, then refresh the TypeScript plugin if it changed.

### A. You installed with `pip install git+https://…` (non-editable)

Re-run the **same** install command with **`--upgrade`** so pip pulls the latest commit for that ref:

**Latest `main`:**

```bash
pip install --upgrade --force-reinstall "git+https://github.com/wtthornton/tapps-brain.git@main#egg=tapps-brain[mcp]"
```

**Move to a newer tag** (change `v1.3.0` to the tag you want):

```bash
pip install --upgrade --force-reinstall "git+https://github.com/wtthornton/tapps-brain.git@v1.3.0#egg=tapps-brain[mcp]"
```

`--force-reinstall` is optional but avoids stale metadata when the version string in `pyproject.toml` did not bump on a branch.

Verify:

```bash
tapps-brain --version
tapps-brain-mcp --version
```

### B. You use an editable clone (`pip install -e ".[mcp]"`)

```bash
cd tapps-brain
git fetch origin
git checkout main          # or a release tag
git pull
pip install -e ".[mcp]"    # pick up pyproject / entry-point changes
cd openclaw-plugin
npm install
npm run build
openclaw plugin install .
```

### C. Restart OpenClaw

After upgrading Python or the plugin, **restart the OpenClaw gateway** (or whatever process spawns `tapps-brain-mcp`) so it starts a fresh MCP child with the new code.

### D. SQLite schema

tapps-brain migrates its project database **automatically** on open when the bundled schema version increases. No separate migration step for normal upgrades.

---

## What OpenClaw “reads”

| Artifact | Role |
|----------|------|
| `openclaw-skill/SKILL.md` | Skill metadata + instructions (often loaded into context for agents) |
| `openclaw-skill/openclaw.plugin.json` | MCP command, `configSchema`, and default `install.pip` (PyPI-style unless you override locally) |
| **This file** | Explicit **Git-only** install and **upgrade** commands — use when you skip PyPI |

If you install from Git **before** running `openclaw skill install …`, the skill’s automated `pip` step may try PyPI again; prefer **manual** Git install (steps 1–3) or adjust your local `openclaw.plugin.json` `install.pip` to the same `git+https://…` URL your environment supports.

## Requirements

- Python **3.12+**
- Node **18+** (for `openclaw-plugin`)
- OpenClaw **v2026.3.7+** for full ContextEngine (recommended)
