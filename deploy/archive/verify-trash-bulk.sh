#!/usr/bin/env bash
# Verifiziert Batch-Operationen im Papierkorb (bulk restore / bulk purge).
set -uo pipefail
cd /home/ifs/ifs
BASE="http://127.0.0.1:8000"
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
py() { python3 -c "$1"; }

TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | py 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
AUTH="Authorization: Bearer $TOKEN"

mk() {
  local name="$1"
  local fid eid
  fid="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"name\":\"$name\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
  eid="$(curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$fid&name=a.txt" \
    -H "$AUTH" --data-binary "inhalt $name" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
  curl -s -o /dev/null -X DELETE "$BASE/api/entries/$fid" -H "$AUTH"
  echo "$fid $eid"
}

A="$(mk "bulk-a-$$")"; AID="${A%% *}"; AFILE="${A##* }"
B="$(mk "bulk-b-$$")"; BID="${B%% *}"; BFILE="${B##* }"
echo "Ordner im Papierkorb: $AID $BID"

echo "== Bulk-Wiederherstellen =="
R="$(curl -s -X POST "$BASE/api/trash/bulk/restore" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"ids\":[\"$AID\",\"$BID\"]}")"
echo "$R"
echo "$R" | py 'import sys,json;d=json.load(sys.stdin);assert d["ok"]==2 and d["failed"]==[], d'
CODE_A="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/entries/$AFILE" -H "$AUTH")"
CODE_B="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/entries/$BFILE" -H "$AUTH")"
echo "Dateien wieder da: a=$CODE_A b=$CODE_B (erwartet 200/200)"

echo "== Erneut löschen und Bulk-Endlöschen =="
curl -s -o /dev/null -X DELETE "$BASE/api/entries/$AID" -H "$AUTH"
curl -s -o /dev/null -X DELETE "$BASE/api/entries/$BID" -H "$AUTH"
P="$(curl -s -X POST "$BASE/api/trash/bulk/purge" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"ids\":[\"$AID\",\"$BID\"]}")"
echo "$P"
echo "$P" | py 'import sys,json;d=json.load(sys.stdin);assert d["ok"]==2 and d["files"]==2 and d["failed"]==[], d'
GONE="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/entries/$AFILE" -H "$AUTH")"
echo "endgültig weg: $GONE (erwartet 404)"

echo "== Teilfehler wird gemeldet =="
C="$(mk "bulk-c-$$")"; CID="${C%% *}"
ACTIVE="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"name\":\"aktiv-$$\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
M="$(curl -s -X POST "$BASE/api/trash/bulk/restore" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"ids\":[\"$CID\",\"$ACTIVE\"]}")"
echo "$M"
echo "$M" | py 'import sys,json;d=json.load(sys.stdin);assert d["ok"]==1 and len(d["failed"])==1, d'
curl -s -o /dev/null -X DELETE "$BASE/api/entries/$ACTIVE" -H "$AUTH"
curl -s -o /dev/null -X DELETE "$BASE/api/trash/$ACTIVE" -H "$AUTH"
curl -s -o /dev/null -X DELETE "$BASE/api/entries/$CID" -H "$AUTH"
curl -s -o /dev/null -X DELETE "$BASE/api/trash/$CID" -H "$AUTH"
echo VERIFY_TRASH_BULK_DONE
