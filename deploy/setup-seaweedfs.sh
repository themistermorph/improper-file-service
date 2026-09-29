#!/usr/bin/env bash
# Erzeugt SeaweedFS-Zugangsdaten (S3) und den Basic-Auth-Login für die Storage-UI.
# Aktualisiert .env und legt seaweedfs/s3.json + seaweedfs/Caddyfile an.
set -euo pipefail
cd /home/ifs/ifs
mkdir -p seaweedfs
# .env enthält Secrets (JWT, Admin-, S3-Zugangsdaten) -> nur für den Eigentümer lesbar.
touch .env
chmod 600 .env

get() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- || true; }
set_kv() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

ACCESS="$(get IFS_S3_ACCESS_KEY)"
SECRET="$(get IFS_S3_SECRET_KEY)"
[ -n "$ACCESS" ] || ACCESS="ifs$(openssl rand -hex 4)"
[ -n "$SECRET" ] || SECRET="$(openssl rand -hex 16)"

UI_USER="$(get IFS_S3_UI_USER)"; UI_USER="${UI_USER:-admin}"
UI_PASS="$(get IFS_S3_UI_PASSWORD)"
if [ -z "$UI_PASS" ]; then
  UI_PASS="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | cut -c1-16)"
fi

# --- SeaweedFS S3-Identitäten ---
cat > seaweedfs/s3.json <<JSON
{
  "identities": [
    {
      "name": "ifs",
      "credentials": [{ "accessKey": "$ACCESS", "secretKey": "$SECRET" }],
      "actions": ["Admin", "Read", "Write", "List", "Tagging"]
    }
  ]
}
JSON
chmod 600 seaweedfs/s3.json

# --- Caddyfile mit Basic Auth (Direktive je nach Caddy-Version) ---
HASH="$(docker run --rm caddy:2 caddy hash-password --plaintext "$UI_PASS" 2>/dev/null | tail -n1)"
if ! printf '%s' "$HASH" | grep -q '^\$2'; then
  echo "FEHLER: bcrypt-Hash konnte nicht erzeugt werden." >&2
  exit 1
fi
CADDY_VERSION="$(docker run --rm caddy:2 caddy version 2>/dev/null | awk '{print $1}')"
DIRECTIVE="basic_auth"
MINOR="$(printf '%s' "$CADDY_VERSION" | sed -E 's/^v2\.([0-9]+).*/\1/')"
if [ -n "$MINOR" ] && [ "$MINOR" -lt 8 ] 2>/dev/null; then
  DIRECTIVE="basicauth"
fi

printf ':9010 {\n\t%s {\n\t\t%s %s\n\t}\n\treverse_proxy s3:8888\n}\n' \
  "$DIRECTIVE" "$UI_USER" "$HASH" > seaweedfs/Caddyfile
chmod 600 seaweedfs/Caddyfile
chmod 700 seaweedfs

# --- .env aktualisieren ---
set_kv IFS_S3_ENDPOINT_URL "http://s3:8333"
set_kv IFS_S3_REGION "us-east-1"
set_kv IFS_S3_BUCKET "ifs"
set_kv IFS_S3_ACCESS_KEY "$ACCESS"
set_kv IFS_S3_SECRET_KEY "$SECRET"
set_kv IFS_S3_USE_SSL "false"
set_kv IFS_S3_SSE ""
set_kv IFS_S3_UI_USER "$UI_USER"
set_kv IFS_S3_UI_PASSWORD "$UI_PASS"
set_kv IFS_SEAWEEDFS_STATUS_URL "http://s3:9333/vol/status"

echo "SeaweedFS vorbereitet."
echo "  S3:        http://s3:8333  (access=$ACCESS)"
# Passwort absichtlich nicht ausgeben (kein Leak in CI-/Terminal-Logs).
echo "  Storage-UI: http://<server>:9010  (Benutzer $UI_USER, Passwort in .env: IFS_S3_UI_PASSWORD)"
