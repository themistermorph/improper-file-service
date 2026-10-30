#!/usr/bin/env bash
# End-to-End-Test: Freigaben-Übersicht und Audit-Log.
set -uo pipefail
BASE="${1:-http://127.0.0.1:8000}"
CRED=/home/ifs/ifs/ADMIN_CREDENTIALS.txt
USER="$(grep '^username=' "$CRED" | cut -d= -f2)"
PASS="$(grep '^password=' "$CRED" | cut -d= -f2)"
py() { python3 -c "$1"; }
JSON='Content-Type: application/json'

TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H "$JSON" \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | py 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
AUTH="Authorization: Bearer $TOKEN"

FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H "$JSON" \
  -d '{"name":"share-test"}' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
EID="$(printf 'teilen\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=teilen.txt" \
  -H "$AUTH" --data-binary @- | py 'import sys,json;print(json.load(sys.stdin)["id"])')"

echo "== Freigabe erstellen =="
SHARE="$(curl -s -X POST "$BASE/api/shares" -H "$AUTH" -H "$JSON" \
  -d "{\"entry_id\":\"$EID\",\"password\":\"geheim\"}")"
TOK="$(echo "$SHARE" | py 'import sys,json;print(json.load(sys.stdin)["token"])')"
echo "token=${TOK:0:12}…"

echo "== Freigaben-Übersicht =="
curl -s -H "$AUTH" "$BASE/api/shares" \
  | py 'import sys,json;d=json.load(sys.stdin);print([(s["entry_path"],s["created_by_username"],s["has_password"]) for s in d])'

echo "== Audit (letzte 3, Akteur) =="
curl -s -H "$AUTH" "$BASE/api/audit?limit=3" \
  | py 'import sys,json;d=json.load(sys.stdin);[print(r["action"],"|",r["actor_username"],"|",r["protocol"]) for r in d]'

echo "== Widerrufen =="
curl -s -X DELETE "$BASE/api/shares/$TOK" -H "$AUTH" -o /dev/null -w 'revoke=%{http_code}\n'

echo "== Aufräumen =="
curl -s -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH" -o /dev/null
curl -s -X DELETE "$BASE/api/trash/$FOLDER" -H "$AUTH" -o /dev/null -w 'purge=%{http_code}\n'
echo VERIFY_SHARES_DONE
