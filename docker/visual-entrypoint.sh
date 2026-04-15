#!/bin/sh
# Injects the HTTP adapter bearer token into nginx config at container start,
# then hands off to the stock nginx entrypoint. The token is read from a
# mounted docker secret; it never appears in the image or in environment
# variables passed to child processes.
set -eu

SECRET_FILE=/run/secrets/tapps_http_auth_token
CONF=/etc/nginx/conf.d/default.conf
PLACEHOLDER=__TAPPS_HTTP_AUTH_TOKEN__

if [ ! -r "$SECRET_FILE" ]; then
  echo "[visual-entrypoint] FATAL: $SECRET_FILE not readable (mount the secret)." >&2
  exit 1
fi

TOKEN=$(tr -d '\r\n' < "$SECRET_FILE")
if [ -z "$TOKEN" ]; then
  echo "[visual-entrypoint] FATAL: auth token secret is empty." >&2
  exit 1
fi

# Escape slashes + ampersands for safe sed replacement.
ESCAPED=$(printf '%s' "$TOKEN" | sed -e 's/[\/&]/\\&/g')
sed -i "s/$PLACEHOLDER/$ESCAPED/g" "$CONF"

unset TOKEN ESCAPED

exec /docker-entrypoint.sh "$@"
