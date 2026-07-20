#!/bin/sh
# Injects the HTTP adapter bearer token into nginx config at container start,
# then hands off to the stock nginx entrypoint.
#
# Token sources, in priority order:
#   1. $TAPPS_BRAIN_AUTH_TOKEN env var (preferred — matches docker/.env pattern)
#   2. /run/secrets/tapps_http_auth_token (legacy docker-secret mount)
#
# The token is substituted into the nginx config file at startup; it never
# appears in environment variables passed to child processes (sanitized below).
#
# Always restore the nginx conf from a pristine template first so restarting
# the container with a rotated TAPPS_BRAIN_AUTH_TOKEN / TAPPS_BRAIN_PROJECT
# actually picks up the new values (sed on already-substituted conf is a no-op).
set -eu

CONF=/etc/nginx/conf.d/default.conf
# Must NOT live under /etc/nginx/templates/ — nginx's stock
# 20-envsubst-on-templates.sh rewrites conf.d from that dir after we sed.
TEMPLATE=/etc/nginx/visual/default.conf.template
INDEX=/usr/share/nginx/html/index.html
TOKEN_PLACEHOLDER=__TAPPS_HTTP_AUTH_TOKEN__
PROJECT_PLACEHOLDER=__TAPPS_HTTP_PROJECT_ID__
OPTIONS_PLACEHOLDER=__TAPPS_PROJECT_OPTIONS__
SECRET_FILE=/run/secrets/tapps_http_auth_token

TOKEN=""
if [ -n "${TAPPS_BRAIN_AUTH_TOKEN:-}" ]; then
  TOKEN="$TAPPS_BRAIN_AUTH_TOKEN"
elif [ -r "$SECRET_FILE" ]; then
  TOKEN=$(tr -d '\r\n' < "$SECRET_FILE")
fi

if [ -z "$TOKEN" ]; then
  echo "[visual-entrypoint] FATAL: no auth token — set TAPPS_BRAIN_AUTH_TOKEN in docker/.env" >&2
  exit 1
fi

PROJECT_ID="${TAPPS_BRAIN_PROJECT:-default}"
# Comma-separated project ids seeded into the dashboard filter (RLS prevents
# enumerating kg_entities tenants from the runtime role).
PROJECT_OPTIONS="${TAPPS_BRAIN_PROJECT_OPTIONS:-$PROJECT_ID}"

# Escape slashes + ampersands for safe sed replacement.
ESCAPED_TOKEN=$(printf '%s' "$TOKEN" | sed -e 's/[\/&]/\\&/g')
ESCAPED_PROJECT=$(printf '%s' "$PROJECT_ID" | sed -e 's/[\/&]/\\&/g')
ESCAPED_OPTIONS=$(printf '%s' "$PROJECT_OPTIONS" | sed -e 's/[\/&]/\\&/g')

# Restore from the non-TLS template only when CONF is a normal writable
# HTTP conf.  Documented TLS mounts bind nginx-visual-tls.conf onto $CONF
# (often :ro); overwriting those wipes HTTPS or fails under set -eu.
if [ -f "$TEMPLATE" ]; then
  if grep -qE 'ssl_certificate|listen[[:space:]]+443' "$CONF" 2>/dev/null; then
    echo "[visual-entrypoint] TLS conf at $CONF — substituting in place (skip template restore)" >&2
  elif [ ! -w "$CONF" ]; then
    echo "[visual-entrypoint] $CONF not writable — substituting in place" >&2
  else
    cp "$TEMPLATE" "$CONF"
  fi
else
  echo "[visual-entrypoint] WARN: missing $TEMPLATE — substituting in place (token rotation on restart may no-op)" >&2
fi

sed -i "s/$TOKEN_PLACEHOLDER/$ESCAPED_TOKEN/g" "$CONF"
sed -i "s/$PROJECT_PLACEHOLDER/$ESCAPED_PROJECT/g" "$CONF"

# Seed the project-filter dropdown options into index.html when the placeholder
# is present (first start or after image rebuild). Also rewrite a previously
# substituted meta content so option-list rotation works across restarts.
if [ -f "$INDEX" ]; then
  if grep -q "$OPTIONS_PLACEHOLDER" "$INDEX" 2>/dev/null; then
    sed -i "s/$OPTIONS_PLACEHOLDER/$ESCAPED_OPTIONS/g" "$INDEX"
  else
    # Already substituted on a prior boot — replace the meta content attribute.
    sed -i "s/\\(name=\"tapps-project-options\" content=\"\\)[^\"]*/\\1$ESCAPED_OPTIONS/" "$INDEX"
  fi
fi

# Sanitize — no child process should see the raw token.
unset TOKEN ESCAPED_TOKEN ESCAPED_PROJECT ESCAPED_OPTIONS PROJECT_ID PROJECT_OPTIONS \
  TAPPS_BRAIN_AUTH_TOKEN TAPPS_BRAIN_PROJECT TAPPS_BRAIN_PROJECT_OPTIONS

exec /docker-entrypoint.sh "$@"
