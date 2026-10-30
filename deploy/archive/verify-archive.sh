#!/usr/bin/env bash
# End-to-End-Test des ZIP-Downloads.
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
  -d '{"name":"zip-test"}' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
SUB="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H "$JSON" \
  -d "{\"parent_id\":\"$FOLDER\",\"name\":\"unterordner\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
printf 'Inhalt A\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=a.txt" -H "$AUTH" --data-binary @- >/dev/null
printf 'Inhalt B\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$SUB&name=b.txt" -H "$AUTH" --data-binary @- >/dev/null

echo "== Archiv-Token =="
INFO="$(curl -s -X POST "$BASE/api/entries/$FOLDER/archive-token" -H "$AUTH")"
echo "$INFO" | py 'import sys,json;d=json.load(sys.stdin);print("name:",d["name"],"expires_in:",d["expires_in"])'
URL="$(echo "$INFO" | py 'import sys,json;print(json.load(sys.stdin)["url"])')"

echo "== ZIP laden (ohne Auth-Header) =="
curl -s -o /tmp/ifs-archive.zip -w 'download=%{http_code} bytes=%{size_download} type=%{content_type}\n' "$BASE$URL"
echo "== Inhalt =="
unzip -l /tmp/ifs-archive.zip | sed -n '1,12p'

curl -s -o /dev/null -w 'ungueltiger_token=%{http_code} (erwartet 401)\n' "$BASE/api/archive/ungueltig"

echo "== Aufräumen =="
curl -s -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH" -o /dev/null
curl -s -X DELETE "$BASE/api/trash/$FOLDER" -H "$AUTH" -o /dev/null -w 'purge=%{http_code}\n'
echo VERIFY_ARCHIVE_DONE
