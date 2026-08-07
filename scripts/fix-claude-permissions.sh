#!/usr/bin/env bash
#
# Fix the Claude Code permission precedence bug that blocks docker/.env.example,
# and add a secrets-safe path for the BRAIN_VERSION bump.
#
#   bash /home/wtthornton/code/tapps-brain/scripts/fix-claude-permissions.sh
#
# WHAT IS ACTUALLY BROKEN
# -----------------------
# .claude/settings.json already has:
#     allow: "Read(.env.example)"
#     deny:  "Read(.env.*)"        <-- catch-all
# In Claude Code, deny ALWAYS beats allow. So the catch-all shadows the explicit
# allow and a tracked, secret-free template becomes unreadable. Every *specific*
# secret pattern (.env, .env.local, .env.production, backups, ...) is listed
# separately in deny already, so the catch-all buys nothing except the bug.
#
# WHAT THIS CHANGES
# -----------------
#   1. Removes ONLY "Read(.env.*)" from permissions.deny.
#      It stays in permissions.ask, so an unrecognised .env.<something> still
#      prompts rather than silently opening. "allow" beats "ask", so the
#      explicitly-listed templates resolve to allowed.
#   2. Adds Edit/Write allows for the template files.
#   3. Installs scripts/set-brain-version.sh and allows the agent to run it.
#      The agent still CANNOT read docker/.env — it invokes a purpose-built,
#      auditable one-line rewriter instead. Secrets never enter its context.
#
# WHAT STAYS PROTECTED (unchanged)
# --------------------------------
#   Read(.env), Read(.env.local), Read(.env.*.local), Read(.env.development),
#   Read(.env.staging), Read(.env.production), Read(.env.test),
#   Read(.env.smoke-backup.*), Read(.env.*backup*), Read(.env.bak*)
#   Bash(rm -rf *), Bash(git push --force *), Bash(git reset --hard *)
#
# This script is idempotent and backs up settings.json before touching it.

set -euo pipefail

REPO="/home/wtthornton/code/tapps-brain"
SETTINGS="$REPO/.claude/settings.json"
HELPER="$REPO/scripts/set-brain-version.sh"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$SETTINGS.bak.$STAMP"

[ -f "$SETTINGS" ] || { echo "ERROR: not found: $SETTINGS" >&2; exit 1; }

echo "==> Backing up settings"
cp -p "$SETTINGS" "$BACKUP"
echo "    $BACKUP"

echo "==> Patching permissions"
python3 - "$SETTINGS" <<'PY'
import json, sys, pathlib

path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
perms = data.setdefault("permissions", {})
deny = perms.setdefault("deny", [])
allow = perms.setdefault("allow", [])
ask = perms.setdefault("ask", [])

CATCHALL = "Read(.env.*)"
removed = False
if CATCHALL in deny:
    deny.remove(CATCHALL)
    removed = True
    print(f"    deny  - {CATCHALL}   (was shadowing the .env.example allow)")
else:
    print(f"    deny    {CATCHALL} already absent")

# Keep the catch-all as an ask so unknown .env.* still prompts.
if CATCHALL not in ask:
    ask.append(CATCHALL)
    print(f"    ask   + {CATCHALL}")

# Templates are tracked and secret-free: allow edit/write, not just read.
for rule in (
    "Edit(.env.example)",
    "Write(.env.example)",
    "Edit(.env.template)",
    "Write(.env.template)",
    "Bash(bash scripts/set-brain-version.sh:*)",
    "Bash(make check-brain-env:*)",
    "Bash(make dev-deploy:*)",
):
    if rule not in allow:
        allow.append(rule)
        print(f"    allow + {rule}")

# Safety assertions: the real secret denies must survive.
must_keep = [
    "Read(.env)", "Read(.env.local)", "Read(.env.production)",
    "Bash(rm -rf *)", "Bash(git push --force *)", "Bash(git reset --hard *)",
]
missing = [r for r in must_keep if r not in deny]
if missing:
    sys.exit(f"ABORT: safety rules would be lost: {missing}")

path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print(f"    removed_catchall={removed}")
PY

echo "==> Validating JSON"
python3 -c "import json;json.load(open('$SETTINGS'));print('    settings.json parses OK')"

echo "==> Installing $HELPER"
mkdir -p "$REPO/scripts"
cat > "$HELPER" <<'HELPER_EOF'
#!/usr/bin/env bash
#
# Sync BRAIN_VERSION in docker/.env and docker/.env.example to pyproject.toml.
#
#   bash scripts/set-brain-version.sh            # take version from pyproject
#   bash scripts/set-brain-version.sh 3.31.0     # or pass it explicitly
#
# Exists so an agent can align the deploy pin WITHOUT being granted read access
# to docker/.env. It rewrites exactly one line and prints only that line back —
# no other key in the file is read, printed, or modified.
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="${1:-}"
if [ -z "$VERSION" ]; then
  VERSION="$(grep -E '^version[[:space:]]*=' pyproject.toml | head -1 | sed 's/.*=[[:space:]]*"\(.*\)"/\1/')"
fi

if ! printf '%s' "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.]+)?$'; then
  echo "ERROR: '$VERSION' is not a semver version" >&2
  exit 1
fi

for f in docker/.env docker/.env.example; do
  if [ ! -f "$f" ]; then
    echo "skip   $f (absent)"
    continue
  fi
  if ! grep -qE '^BRAIN_VERSION=' "$f"; then
    echo "ERROR: $f has no BRAIN_VERSION line" >&2
    exit 1
  fi
  cp -p "$f" "$f.bak.$(date +%Y%m%d-%H%M%S)"
  sed -i "s/^BRAIN_VERSION=.*/BRAIN_VERSION=$VERSION/" "$f"
  echo "set    $f -> $(grep -E '^BRAIN_VERSION=' "$f")"
done

echo "done   BRAIN_VERSION=$VERSION"
HELPER_EOF
chmod +x "$HELPER"
echo "    installed and chmod +x"

echo
echo "==> Result"
python3 - "$SETTINGS" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))["permissions"]
print("  deny :", len(p.get("deny", [])), "rules  (all secret patterns retained)")
print("  allow:", len(p.get("allow", [])), "rules")
print("  ask  :", len(p.get("ask", [])), "rules")
PY

cat <<'NEXT'

==> Next
  1. Restart Claude Code (or /reload) so the new settings take effect.
  2. Verify with:  bash scripts/set-brain-version.sh
     It should report the current pyproject version for both env files.
  3. The agent still cannot READ docker/.env. That is deliberate — it holds
     live secrets. It can only run the one-line rewriter above.

  Rollback:  cp <the .bak file printed at the top> .claude/settings.json
NEXT
