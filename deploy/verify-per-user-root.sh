#!/usr/bin/env bash
# Verifiziert Per-User-Wurzeln: Isolation, "Mit mir geteilt", Admin ohne Dateizugriff.
set -uo pipefail
cd /home/ifs/ifs
BASE="http://127.0.0.1:8000"
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
py() { python3 -c "$1"; }
J='Content-Type: application/json'
login() { curl -s -X POST "$BASE/api/auth/login" -H "$J" -d "{\"username\":\"$1\",\"password\":\"$2\"}" | py 'import sys,json;print(json.load(sys.stdin).get("access_token",""))'; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

AUTH="Authorization: Bearer $(login "$USER" "$PASS")"
SUF="$$"
ALICE_ID="$(curl -s -X POST "$BASE/api/users" -H "$AUTH" -H "$J" -d "{\"username\":\"alice-$SUF\",\"password\":\"geheim123\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
BOB_ID="$(curl -s -X POST "$BASE/api/users" -H "$AUTH" -H "$J" -d "{\"username\":\"bob-$SUF\",\"password\":\"geheim123\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
ALICE="Authorization: Bearer $(login "alice-$SUF" geheim123)"
BOB="Authorization: Bearer $(login "bob-$SUF" geheim123)"

ALICE_ROOT="$(curl -s -H "$ALICE" "$BASE/api/root" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$ALICE" -H "$J" -d '{"name":"alice-docs"}' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
FILE="$(printf 'geheim\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=secret.txt" -H "$ALICE" --data-binary @- | py 'import sys,json;print(json.load(sys.stdin)["id"])')"

echo "== Isolation =="
echo "bob eigene Liste:  $(curl -s -H "$BOB" "$BASE/api/entries" | py 'import sys,json;print(json.load(sys.stdin))')"
echo "bob fremde Wurzel: $(code -H "$BOB" "$BASE/api/entries?parent_id=$ALICE_ROOT") (erwartet 403)"
echo "bob fremde Datei:  $(code -H "$BOB" "$BASE/api/entries/$FILE") (erwartet 403)"
echo "admin fremde Wurzel: $(code -H "$AUTH" "$BASE/api/entries?parent_id=$ALICE_ROOT") (erwartet 403)"
echo "admin fremde Datei:  $(code -H "$AUTH" "$BASE/api/entries/$FILE") (erwartet 403)"
echo "admin Systemfunktion (users): $(code -H "$AUTH" "$BASE/api/users") (erwartet 200)"

echo "== Mit mir geteilt =="
echo "bob /api/shared vorher: $(curl -s -H "$BOB" "$BASE/api/shared" | py 'import sys,json;print([e["name"] for e in json.load(sys.stdin)])')"
curl -s -o /dev/null -X POST "$BASE/api/entries/$FOLDER/acl" -H "$ALICE" -H "$J" \
  -d "{\"principal_type\":\"user\",\"principal_id\":\"$BOB_ID\",\"perms\":[\"read\",\"write\"]}"
echo "bob /api/shared nachher: $(curl -s -H "$BOB" "$BASE/api/shared" | py 'import sys,json;print([e["name"] for e in json.load(sys.stdin)])')"
echo "bob öffnet geteilt: $(code -H "$BOB" "$BASE/api/entries?parent_id=$FOLDER") (erwartet 200)"

echo "== Aufräumen =="
curl -s -o /dev/null -X DELETE "$BASE/api/entries/$FOLDER" -H "$ALICE"
curl -s -o /dev/null -X DELETE "$BASE/api/trash/$FOLDER" -H "$ALICE"
curl -s -o /dev/null -X DELETE "$BASE/api/users/$ALICE_ID" -H "$AUTH"
curl -s -o /dev/null -X DELETE "$BASE/api/users/$BOB_ID" -H "$AUTH"
echo VERIFY_PER_USER_ROOT_DONE
