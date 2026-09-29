#!/usr/bin/env bash
# End-to-End-Smoke-Test gegen die lokal laufende API (auf dem Server ausführen).
set -uo pipefail

BASE="${1:-http://127.0.0.1:8000}"
CRED=/home/ifs/ifs/ADMIN_CREDENTIALS.txt

if [ ! -f "$CRED" ]; then
  echo "Zugangsdatei fehlt: $CRED"
  exit 1
fi
USER="$(grep '^username=' "$CRED" | cut -d= -f2)"
PASS="$(grep '^password=' "$CRED" | cut -d= -f2)"

echo "== health =="
curl -sS -m 10 "$BASE/healthz"; echo
curl -sS -m 10 "$BASE/version"; echo

TOKEN="$(curl -sS -m 10 -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("access_token",""))')"
if [ -z "$TOKEN" ]; then
  echo "LOGIN_FAIL"
  exit 1
fi
echo "LOGIN_OK (user=$USER)"
AUTH="Authorization: Bearer $TOKEN"
CT='Content-Type: application/json'

echo "== Wurzel auflisten =="
curl -sS -m 10 -H "$AUTH" "$BASE/api/entries"; echo

NAME="smoke-$(date +%s)"
FOLDER="$(curl -sS -m 10 -X POST "$BASE/api/folders" -H "$AUTH" -H "$CT" \
  -d "{\"name\":\"$NAME\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("id",""))')"
echo "Ordner: $NAME -> $FOLDER"

printf 'hello from ifs smoke test\n' > /tmp/ifs-smoke.txt
UP="$(curl -sS -m 20 -X PUT \
  "$BASE/api/uploads/simple?parent_id=$FOLDER&name=smoke.txt" \
  -H "$AUTH" --data-binary @/tmp/ifs-smoke.txt)"
echo "Upload: $UP"
EID="$(echo "$UP" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("id",""))')"

echo "== Download (voller Inhalt) =="
curl -sS -m 10 -H "$AUTH" "$BASE/api/entries/$EID/content"; echo

echo "== Download (Range bytes=0-4) =="
curl -sS -m 10 -H "$AUTH" -H 'Range: bytes=0-4' \
  "$BASE/api/entries/$EID/content"; echo

echo "== HEAD =="
curl -sS -m 10 -I -H "$AUTH" "$BASE/api/entries/$EID/content" \
  | grep -iE 'HTTP/|etag|accept-ranges|content-length|content-type' || true

echo "== Listing im Ordner =="
curl -sS -m 10 -H "$AUTH" "$BASE/api/entries?parent_id=$FOLDER"; echo

echo "VERIFY_DONE"
