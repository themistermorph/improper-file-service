#!/usr/bin/env bash
# Verifiziert Batch-Aktionen für Benutzer und Freigaben.
set -uo pipefail
cd /home/ifs/ifs
BASE="http://127.0.0.1:8000"
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
py() { python3 -c "$1"; }
J='Content-Type: application/json'

TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H "$J" -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | py 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
AH="Authorization: Bearer $TOKEN"
SUF="$$"

mkuser() {
  curl -s -X POST "$BASE/api/users" -H "$AH" -H "$J" \
    -d "{\"username\":\"$1\",\"password\":\"geheim123\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])'
}

echo "== Benutzer: Bulk aktivieren/deaktivieren/löschen =="
U1="$(mkuser "bulk1-$SUF")"
U2="$(mkuser "bulk2-$SUF")"
R="$(curl -s -X POST "$BASE/api/users/bulk/active" -H "$AH" -H "$J" -d "{\"ids\":[\"$U1\",\"$U2\"],\"active\":false}")"
echo "deaktivieren: $R"
L1="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/auth/login" -H "$J" -d "{\"username\":\"bulk1-$SUF\",\"password\":\"geheim123\"}")"
R="$(curl -s -X POST "$BASE/api/users/bulk/active" -H "$AH" -H "$J" -d "{\"ids\":[\"$U1\",\"$U2\"],\"active\":true}")"
echo "aktivieren: $R | Login danach: $L1 -> $(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/auth/login" -H "$J" -d "{\"username\":\"bulk1-$SUF\",\"password\":\"geheim123\"}")"

ADMIN_ID="$(curl -s -H "$AH" "$BASE/api/users?q=$USER" | py 'import sys,json;print(json.load(sys.stdin)["items"][0]["id"])')"
GUARD="$(curl -s -X POST "$BASE/api/users/bulk/active" -H "$AH" -H "$J" -d "{\"ids\":[\"$ADMIN_ID\"],\"active\":false}")"
echo "letzter Admin geschützt: $GUARD"

DEL="$(curl -s -X POST "$BASE/api/users/bulk/delete" -H "$AH" -H "$J" -d "{\"ids\":[\"$U1\",\"$U2\"]}")"
echo "löschen: $DEL"
GONE="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/users/$U1" -H "$AH")"
echo "danach GET user: $GONE (erwartet 404)"

echo "== Freigaben: Bulk-Widerruf =="
F="$(curl -s -X POST "$BASE/api/folders" -H "$AH" -H "$J" -d "{\"name\":\"shbulk-$SUF\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
T1="$(curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$F&name=a.txt" -H "$AH" --data-binary 'eins' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
T2="$(curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$F&name=b.txt" -H "$AH" --data-binary 'zwei' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
S1="$(curl -s -X POST "$BASE/api/shares" -H "$AH" -H "$J" -d "{\"entry_id\":\"$T1\"}" | py 'import sys,json;print(json.load(sys.stdin)["token"])')"
S2="$(curl -s -X POST "$BASE/api/shares" -H "$AH" -H "$J" -d "{\"entry_id\":\"$T2\"}" | py 'import sys,json;print(json.load(sys.stdin)["token"])')"
REV="$(curl -s -X POST "$BASE/api/shares/bulk/revoke" -H "$AH" -H "$J" -d "{\"tokens\":[\"$S1\",\"$S2\",\"unbekannt\"]}")"
echo "widerrufen: $REV"
META="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/shares/$S1")"
echo "Link danach: $META (erwartet 404)"

curl -s -o /dev/null -X DELETE "$BASE/api/entries/$F" -H "$AH"
curl -s -o /dev/null -X DELETE "$BASE/api/trash/$F" -H "$AH"
echo VERIFY_BULK_ADMIN_DONE
