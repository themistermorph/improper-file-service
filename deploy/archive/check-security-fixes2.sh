#!/usr/bin/env bash
# Prüft die Fixes F1 (Share-Upload/Overwrite) und F2 (Move braucht read) live.
set -uo pipefail
cd /home/ifs/ifs
BASE="http://127.0.0.1:8000"
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
py() { python3 -c "$1"; }
J='Content-Type: application/json'
jget() { python3 -c "import sys,json;d=json.load(sys.stdin);print(d$1)"; }

ADMIN="$(curl -s -X POST "$BASE/api/auth/login" -H "$J" -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | jget "['access_token']")"
AH="Authorization: Bearer $ADMIN"
SUF="$$"
BOB_ID="$(curl -s -X POST "$BASE/api/users" -H "$AH" -H "$J" -d "{\"username\":\"bob$SUF\",\"password\":\"geheim123\"}" | jget "['id']")"
BOB="$(curl -s -X POST "$BASE/api/auth/login" -H "$J" -d "{\"username\":\"bob$SUF\",\"password\":\"geheim123\"}" | jget "['access_token']")"
BH="Authorization: Bearer $BOB"

echo "== F1a: allow_upload braucht write =="
FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$AH" -H "$J" -d "{\"name\":\"dropchk-$SUF\"}" | jget "['id']")"
SHARER="$(curl -s -H "$AH" "$BASE/api/roles?scope=resource" | python3 -c "import sys,json;print(next(r['id'] for r in json.load(sys.stdin) if r['name']=='sharer'))")"
curl -s -o /dev/null -X POST "$BASE/api/entries/$FOLDER/roles" -H "$AH" -H "$J" \
  -d "{\"role_id\":\"$SHARER\",\"principal_type\":\"user\",\"principal_id\":\"$BOB_ID\"}"
DEN="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/shares" -H "$BH" -H "$J" -d "{\"entry_id\":\"$FOLDER\",\"allow_upload\":true}")"
OKJ="$(curl -s -X POST "$BASE/api/shares" -H "$BH" -H "$J" -d "{\"entry_id\":\"$FOLDER\"}")"
OK="$(echo "$OKJ" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["allow_upload"],d["overwrite"])')"
echo "drop-link ohne write: $DEN (erwartet 403) | einfache Freigabe (allow_upload overwrite): $OK"

echo "== F1b: Overwrite ist Freigabe-Eigenschaft =="
F2="$(curl -s -X POST "$BASE/api/folders" -H "$AH" -H "$J" -d "{\"name\":\"overw-$SUF\"}" | jget "['id']")"
curl -s -o /dev/null -X PUT "$BASE/api/uploads/simple?parent_id=$F2&name=keep.txt" -H "$AH" --data-binary 'alt'
T2="$(curl -s -X POST "$BASE/api/shares" -H "$AH" -H "$J" -d "{\"entry_id\":\"$F2\",\"allow_upload\":true}" | jget "['token']")"
N1="$(curl -s -X POST "$BASE/api/shares/$T2/upload?name=keep.txt&overwrite=true" --data-binary 'neu' | jget "['name']")"
curl -s -o /dev/null -X PATCH "$BASE/api/shares/$T2" -H "$AH" -H "$J" -d '{"overwrite":true}'
N2="$(curl -s -X POST "$BASE/api/shares/$T2/upload?name=keep.txt" --data-binary 'neu2' | jget "['name']")"
echo "nur Parameter -> $N1 (erwartet keep (2).txt) | Freigabe-Eigenschaft -> $N2 (erwartet keep.txt)"

echo "== F2: Move braucht read auf der Quelle =="
PRIV="$(curl -s -X POST "$BASE/api/folders" -H "$AH" -H "$J" -d "{\"name\":\"priv-$SUF\"}" | jget "['id']")"
EXF="$(curl -s -X POST "$BASE/api/folders" -H "$AH" -H "$J" -d "{\"name\":\"exfil-$SUF\"}" | jget "['id']")"
curl -s -o /dev/null -X POST "$BASE/api/entries/$PRIV/acl" -H "$AH" -H "$J" \
  -d "{\"principal_type\":\"user\",\"principal_id\":\"$BOB_ID\",\"perms\":[\"write\"]}"
curl -s -o /dev/null -X POST "$BASE/api/entries/$EXF/acl" -H "$AH" -H "$J" \
  -d "{\"principal_type\":\"user\",\"principal_id\":\"$BOB_ID\",\"perms\":[\"read\",\"write\"]}"
SECRET="$(curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$PRIV&name=secret.txt" -H "$AH" --data-binary 'secret' | jget "['id']")"
MV="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/entries/$SECRET/move" -H "$BH" -H "$J" -d "{\"parent_id\":\"$EXF\"}")"
BM="$(curl -s -X POST "$BASE/api/bulk/move" -H "$BH" -H "$J" -d "{\"ids\":[\"$SECRET\"],\"parent_id\":\"$EXF\"}")"
echo "move ohne read: $MV (erwartet 403) | bulk: $BM"

# Aufräumen
for f in "$FOLDER" "$F2" "$PRIV" "$EXF"; do
  curl -s -o /dev/null -X DELETE "$BASE/api/entries/$f" -H "$AH"
  curl -s -o /dev/null -X DELETE "$BASE/api/trash/$f" -H "$AH"
done
curl -s -o /dev/null -X DELETE "$BASE/api/users/$BOB_ID" -H "$AH"
echo CHECK_SECURITY_FIXES2_DONE
