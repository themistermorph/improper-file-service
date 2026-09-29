#!/usr/bin/env bash
# End-to-End-Test des Papierkorbs gegen die lokal laufende API.
set -uo pipefail
BASE="${1:-http://127.0.0.1:8000}"
CRED=/home/ifs/ifs/ADMIN_CREDENTIALS.txt
USER="$(grep '^username=' "$CRED" | cut -d= -f2)"
PASS="$(grep '^password=' "$CRED" | cut -d= -f2)"

TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
AUTH="Authorization: Bearer $TOKEN"
JSON='Content-Type: application/json'

echo "== Zähler vor =="
curl -s -H "$AUTH" "$BASE/api/trash/count"; echo

NAME="trash-$(date +%s)"
FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H "$JSON" \
  -d "{\"name\":\"$NAME\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')"

printf 'trash demo\n' | curl -s -X PUT \
  "$BASE/api/uploads/simple?parent_id=$FOLDER&name=a.txt" \
  -H "$AUTH" --data-binary @- >/dev/null

echo "== Löschen =="
curl -s -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH" -o /dev/null -w 'delete=%{http_code}\n'

echo "== Papierkorb =="
curl -s -H "$AUTH" "$BASE/api/trash" | python3 -m json.tool

echo "== Wiederherstellen =="
curl -s -X POST "$BASE/api/trash/$FOLDER/restore" -H "$AUTH" -H "$JSON" -d '{}' \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("restored:",d.get("path"),d.get("type"))'

echo "== Endgültig löschen =="
curl -s -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH" -o /dev/null
curl -s -X DELETE "$BASE/api/trash/$FOLDER" -H "$AUTH" -o /dev/null -w 'purge=%{http_code}\n'

echo "== Zähler nach =="
curl -s -H "$AUTH" "$BASE/api/trash/count"; echo

echo VERIFY_TRASH_DONE
