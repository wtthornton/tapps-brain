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
set -eu

CONF=/etc/nginx/conf.d/default.conf
PLACEHOLDER=__TAPPS_HTTP_AUTH_TOKEN__
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

# Escape slashes + ampersands for safe sed replacement.
ESCAPED=$(printf '%s' "$TOKEN" | sed -e 's/[\/&]/\\&/g')
sed -i "s/$PLACEHOLDER/$ESCAPED/g" "$CONF"

# Sanitize — no child process should see the raw token.
unset TOKEN ESCAPED TAPPS_BRAIN_AUTH_TOKEN

exec /docker-entrypoint.sh "$@"
