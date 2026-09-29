#!/usr/bin/env bash
# End-to-End-Test: Versionshistorie und Rollback.
set -uo pipefail
cd /home/ifs/ifs
BASE="http://127.0.0.1:8000"
CRED=ADMIN_CREDENTIALS.txt
USER="$(grep '^username=' "$CRED" | cut -d= -f2)"
PASS="$(grep '^password=' "$CRED" | cut -d= -f2)"
py() { python3 -c "$1"; }
JSON='Content-Type: application/json'

TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H "$JSON" \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | py 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
AUTH="Authorization: Bearer $TOKEN"

FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H "$JSON" -d '{"name":"version-test"}' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
URL="$BASE/api/uploads/simple?parent_id=$FOLDER&name=note.txt"
printf 'Inhalt v1\n' | curl -s -X PUT "$URL" -H "$AUTH" --data-binary @- >/dev/null
EID="$(printf 'Inhalt v2\n' | curl -s -X PUT "$URL" -H "$AUTH" --data-binary @- | py 'import sys,json;print(json.load(sys.stdin)["id"])')"

echo "== Historie =="
curl -s -H "$AUTH" "$BASE/api/entries/$EID/versions" \
  | py 'import sys,json;d=json.load(sys.stdin);print([(v["seq"],v["size"],v["is_current"]) for v in d])'
V1="$(curl -s -H "$AUTH" "$BASE/api/entries/$EID/versions" | py 'import sys,json;d=json.load(sys.stdin);print(d[-1]["id"])')"

echo "== Aktueller Inhalt =="
curl -s -H "$AUTH" "$BASE/api/entries/$EID/content"

echo "== Inhalt der alten Version =="
curl -s -H "$AUTH" "$BASE/api/entries/$EID/versions/$V1/content"

echo "== Rollback auf Version 1 =="
curl -s -X POST "$BASE/api/entries/$EID/versions/$V1/restore" -H "$AUTH" \
  | py 'import sys,json;d=json.load(sys.stdin);print("neue Version seq",d["seq"],"current",d["is_current"])'
echo -n "aktueller Inhalt danach: "; curl -s -H "$AUTH" "$BASE/api/entries/$EID/content"
curl -s -H "$AUTH" "$BASE/api/entries/$EID/versions" \
  | py 'import sys,json;d=json.load(sys.stdin);print("Historie:",[v["seq"] for v in d])'

echo "== Aufräumen =="
curl -s -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH" -o /dev/null
curl -s -X DELETE "$BASE/api/trash/$FOLDER" -H "$AUTH" -o /dev/null
echo VERIFY_VERSIONS_DONE
