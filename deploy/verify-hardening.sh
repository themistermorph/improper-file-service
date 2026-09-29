#!/usr/bin/env bash
# Verifiziert Härtungen 6–9 und 13 auf dem Testsystem.
set -uo pipefail
BASE="http://127.0.0.1:8000"
CRED=/home/ifs/ifs/ADMIN_CREDENTIALS.txt
USER="$(grep '^username=' "$CRED" | cut -d= -f2)"
PASS="$(grep '^password=' "$CRED" | cut -d= -f2)"
py() { python3 -c "$1"; }
JSON='Content-Type: application/json'

echo "== Info-Endpunkte =="
curl -s "$BASE/healthz"; echo
curl -s -o /dev/null -w 'docs=%{http_code} (erwartet 404)\n' "$BASE/docs"
curl -s -o /dev/null -w 'openapi=%{http_code} (erwartet 404)\n' "$BASE/openapi.json"
curl -s -o /dev/null -w 'metrics_ohne_auth=%{http_code} (erwartet 401)\n' "$BASE/metrics"

TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H "$JSON" \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | py 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
AUTH="Authorization: Bearer $TOKEN"
curl -s -o /dev/null -w 'metrics_mit_admin=%{http_code} (erwartet 200)\n' -H "$AUTH" "$BASE/metrics"

echo "== Security-Header =="
curl -s -D - -o /dev/null "$BASE/" | grep -iE 'content-security-policy|x-frame-options|referrer-policy|x-content-type-options' | sed 's/:.*/: <gesetzt>/'

echo "== Robuste Eingaben =="
FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H "$JSON" -d '{"name":"hardening"}' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
EID="$(printf 'x\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=f.txt" -H "$AUTH" --data-binary @- | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
curl -s -o /dev/null -w 'range_ungueltig=%{http_code} (erwartet 416)\n' -H "$AUTH" -H 'Range: bytes=abc-def' "$BASE/api/entries/$EID/content"
SESSION="$(curl -s -X POST "$BASE/api/uploads" -H "$AUTH" -H "$JSON" -d "{\"parent_id\":\"$FOLDER\",\"name\":\"u.txt\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
curl -s -o /dev/null -w 'offset_ungueltig=%{http_code} (erwartet 400)\n' -X PATCH "$BASE/api/uploads/$SESSION" -H "$AUTH" -H 'Upload-Offset: abc' --data-binary 'x'

echo "== Plausibilität =="
BOGUS="$(python3 -c 'import uuid;print(uuid.uuid4())')"
curl -s -o /dev/null -w 'acl_bogus=%{http_code} (erwartet 400)\n' -X POST "$BASE/api/entries/$FOLDER/acl" \
  -H "$AUTH" -H "$JSON" -d "{\"principal_type\":\"user\",\"principal_id\":\"$BOGUS\",\"perms\":[\"read\"]}"
curl -s -o /dev/null -w 'copy_in_sich=%{http_code} (erwartet 400)\n' -X POST "$BASE/api/entries/$FOLDER/copy" \
  -H "$AUTH" -H "$JSON" -d "{\"parent_id\":\"$FOLDER\",\"name\":\"x\"}"

echo "== Aufräumen =="
curl -s -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH" -o /dev/null
curl -s -X DELETE "$BASE/api/trash/$FOLDER" -H "$AUTH" -o /dev/null
echo VERIFY_HARDENING_DONE
