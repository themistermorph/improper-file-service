#!/usr/bin/env bash
# Prüft gezielt die neuen Security-Fix-Verhalten (N1, N2) gegen die laufende API.
set -uo pipefail
cd /home/ifs/ifs
BASE="http://127.0.0.1:8000"
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
py() { python3 -c "$1"; }

echo "== N1: Archiv-Job-Token wird geprüft =="
CODE="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/archive/jobs/kein-token")"
echo "ungueltiger Token: $CODE (erwartet 401)"

echo "== N2: Share-Metadaten nur mit Passwort (no-store) =="
TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | py 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
AUTH="Authorization: Bearer $TOKEN"
FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"name\":\"fixcheck-$$\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
ENTRY="$(curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=f.txt" \
  -H "$AUTH" --data-binary 'x' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
STOKEN="$(curl -s -X POST "$BASE/api/shares" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"entry_id\":\"$ENTRY\",\"password\":\"pw\"}" | py 'import sys,json;print(json.load(sys.stdin)["token"])')"
NO="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/shares/$STOKEN")"
OK="$(curl -s -o /dev/null -w '%{http_code}' -H 'X-Share-Password: pw' "$BASE/api/shares/$STOKEN")"
CACHE="$(curl -s -D - -o /dev/null -H 'X-Share-Password: pw' "$BASE/api/shares/$STOKEN" | grep -i '^cache-control' | tr -d '\r')"
echo "ohne Passwort: $NO (erwartet 401) | mit Passwort: $OK (erwartet 200)"
echo "$CACHE"

curl -s -o /dev/null -X DELETE "$BASE/api/shares/$STOKEN" -H "$AUTH"
curl -s -o /dev/null -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH"
echo CHECK_SECURITY_FIXES_DONE
