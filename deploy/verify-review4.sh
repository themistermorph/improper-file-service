#!/usr/bin/env bash
# Verifiziert die Fixes S1-S4 aus dem vierten Sicherheits-Review.
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
ALICE_NAME="s4a-$SUF"
BOB_NAME="s4b-$SUF"
ADMIN_ID="$(curl -s -H "$AUTH" "$BASE/api/users?q=$USER" | py 'import sys,json;print(json.load(sys.stdin)["items"][0]["id"])')"
ALICE_ID="$(curl -s -X POST "$BASE/api/users" -H "$AUTH" -H "$J" -d "{\"username\":\"$ALICE_NAME\",\"password\":\"geheim123\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
BOB_ID="$(curl -s -X POST "$BASE/api/users" -H "$AUTH" -H "$J" -d "{\"username\":\"$BOB_NAME\",\"password\":\"geheim123\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
ALICE="Authorization: Bearer $(login "$ALICE_NAME" geheim123)"
BOB="Authorization: Bearer $(login "$BOB_NAME" geheim123)"

ALICE_ROOT="$(curl -s -H "$ALICE" "$BASE/api/root" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
ADMIN_ROOT="$(curl -s -H "$AUTH" "$BASE/api/root" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$ALICE" -H "$J" -d '{"name":"s4-folder"}' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
FILE="$(printf 'secret\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=secret.txt" -H "$ALICE" --data-binary @- | py 'import sys,json;print(json.load(sys.stdin)["id"])')"

echo "== S1: share ohne read -> kein Link =="
curl -s -o /dev/null -X POST "$BASE/api/entries/$FOLDER/acl" -H "$ALICE" -H "$J" \
  -d "{\"principal_type\":\"user\",\"principal_id\":\"$BOB_ID\",\"perms\":[\"share\"]}"
echo "create_share (nur share): $(code -X POST "$BASE/api/shares" -H "$BOB" -H "$J" -d "{\"entry_id\":\"$FOLDER\"}") (erwartet 403)"

echo "== S2a: Admin weist sich Ressourcenrolle an fremder Wurzel zu =="
MANAGER="$(curl -s -H "$AUTH" "$BASE/api/roles?scope=resource" | py 'import sys,json;print(next(r["id"] for r in json.load(sys.stdin) if r["name"]=="manager"))')"
echo "assign (fremde Wurzel): $(code -X POST "$BASE/api/roles/$MANAGER/assignments" -H "$AUTH" -H "$J" -d "{\"principal_type\":\"user\",\"principal_id\":\"$ADMIN_ID\",\"entry_id\":\"$ALICE_ROOT\"}") (erwartet 403)"
echo "assign (eigene Wurzel): $(code -X POST "$BASE/api/roles/$MANAGER/assignments" -H "$AUTH" -H "$J" -d "{\"principal_type\":\"user\",\"principal_id\":\"$ADMIN_ID\",\"entry_id\":\"$ADMIN_ROOT\"}") (erwartet 201)"

echo "== S2b: Admin darf fremdes Share-Passwort nicht ändern, aber widerrufen =="
STOK="$(curl -s -X POST "$BASE/api/shares" -H "$ALICE" -H "$J" -d "{\"entry_id\":\"$FILE\",\"password\":\"pw\"}" | py 'import sys,json;print(json.load(sys.stdin)["token"])')"
echo "PATCH fremd:  $(code -X PATCH "$BASE/api/shares/$STOK" -H "$AUTH" -H "$J" -d '{"password":""}') (erwartet 403)"
echo "DELETE fremd: $(code -X DELETE "$BASE/api/shares/$STOK" -H "$AUTH") (erwartet 204)"
echo "danach meta:  $(code "$BASE/api/shares/$STOK") (erwartet 404)"

echo "== S4: strenge Längen/Formate =="
LONGTOK="$(printf 'x%.0s' $(seq 1 65))"
echo "bulk-revoke 65 Zeichen: $(code -X POST "$BASE/api/shares/bulk/revoke" -H "$AUTH" -H "$J" -d "{\"tokens\":[\"$LONGTOK\"]}") (erwartet 422)"
echo "upload sha256 ungültig: $(code -X POST "$BASE/api/uploads" -H "$AUTH" -H "$J" -d "{\"parent_id\":\"$ADMIN_ROOT\",\"name\":\"x.bin\",\"sha256\":\"zz\"}") (erwartet 422)"

echo "== Aufräumen =="
curl -s -o /dev/null -X DELETE "$BASE/api/entries/$FOLDER" -H "$ALICE"
curl -s -o /dev/null -X DELETE "$BASE/api/trash/$FOLDER" -H "$ALICE"
curl -s -o /dev/null -X DELETE "$BASE/api/users/$ALICE_ID" -H "$AUTH"
curl -s -o /dev/null -X DELETE "$BASE/api/users/$BOB_ID" -H "$AUTH"
echo VERIFY_REVIEW4_DONE
