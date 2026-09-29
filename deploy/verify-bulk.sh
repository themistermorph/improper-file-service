#!/usr/bin/env bash
# End-to-End-Test der Bulk-Aktionen.
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

SRC="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H "$JSON" -d '{"name":"bulk-src"}' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
DST="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H "$JSON" -d '{"name":"bulk-dst"}' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
F1="$(printf 'eins\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$SRC&name=eins.txt" -H "$AUTH" --data-binary @- | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
F2="$(printf 'zwei\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$SRC&name=zwei.txt" -H "$AUTH" --data-binary @- | py 'import sys,json;print(json.load(sys.stdin)["id"])')"

echo "== Ordnerliste =="
curl -s -H "$AUTH" "$BASE/api/folders" | py 'import sys,json;d=json.load(sys.stdin);print(sorted(f["path"] for f in d if f["path"] in ("/bulk-src","/bulk-dst")))'

echo "== Bulk-Verschieben =="
curl -s -X POST "$BASE/api/bulk/move" -H "$AUTH" -H "$JSON" \
  -d "{\"ids\":[\"$F1\",\"$F2\"],\"parent_id\":\"$DST\"}" | py 'import sys,json;print(json.load(sys.stdin))'
curl -s -H "$AUTH" "$BASE/api/entries?parent_id=$DST" | py 'import sys,json;print("in bulk-dst:",sorted(e["name"] for e in json.load(sys.stdin)))'

echo "== Sammel-ZIP =="
URL="$(curl -s -X POST "$BASE/api/archive/bulk-token" -H "$AUTH" -H "$JSON" \
  -d "{\"entry_ids\":[\"$SRC\",\"$DST\"]}" | py 'import sys,json;print(json.load(sys.stdin)["url"])')"
curl -s -o /tmp/bulk.zip -w 'zip=%{http_code} bytes=%{size_download} type=%{content_type}\n' "$BASE$URL"
unzip -l /tmp/bulk.zip | sed -n '4,9p'

echo "== Bulk-Löschen =="
curl -s -X POST "$BASE/api/bulk/delete" -H "$AUTH" -H "$JSON" \
  -d "{\"ids\":[\"$F1\",\"$F2\"]}" | py 'import sys,json;print(json.load(sys.stdin))'
curl -s -H "$AUTH" "$BASE/api/entries?parent_id=$DST" | py 'import sys,json;print("in bulk-dst danach:",len(json.load(sys.stdin)))'

echo "== Aufräumen =="
for F in "$SRC" "$DST"; do
  curl -s -X DELETE "$BASE/api/entries/$F" -H "$AUTH" -o /dev/null
  curl -s -X DELETE "$BASE/api/trash/$F" -H "$AUTH" -o /dev/null
done
echo "purge ok"
echo VERIFY_BULK_DONE
