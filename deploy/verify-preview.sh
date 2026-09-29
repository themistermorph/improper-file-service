#!/usr/bin/env bash
# End-to-End-Test der Datei-Vorschau gegen die lokal laufende API.
set -uo pipefail
BASE="${1:-http://127.0.0.1:8000}"
CRED=/home/ifs/ifs/ADMIN_CREDENTIALS.txt
USER="$(grep '^username=' "$CRED" | cut -d= -f2)"
PASS="$(grep '^password=' "$CRED" | cut -d= -f2)"
py() { python3 -c "$1"; }

TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | py 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
AUTH="Authorization: Bearer $TOKEN"
JSON='Content-Type: application/json'

FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H "$JSON" \
  -d '{"name":"preview-test"}' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"

echo "== Textdatei hochladen =="
printf '# Titel\nZeile 1\nZeile 2\n' > /tmp/preview.md
EID="$(curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=preview.md" \
  -H "$AUTH" --data-binary @/tmp/preview.md | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
curl -s -H "$AUTH" "$BASE/api/entries/$EID" | py 'import sys,json;d=json.load(sys.stdin);print("mime:",d["mime"],"size:",d["size"])'

echo "== Preview-Token + Abruf ohne Auth =="
URL="$(curl -s -X POST "$BASE/api/entries/$EID/preview-token" -H "$AUTH" | py 'import sys,json;print(json.load(sys.stdin)["url"])')"
echo "url=$URL"
curl -s -o /dev/null -w 'preview_ohne_auth=%{http_code}\n' "$BASE$URL"
curl -s -H 'Range: bytes=0-6' -o /dev/null -w 'preview_range=%{http_code}\n' "$BASE$URL"
echo -n "inhalt: "; curl -s "$BASE$URL" | head -n 1
curl -s -o /dev/null -w 'ungueltiger_token=%{http_code} (erwartet 401)\n' "$BASE/api/preview/ungueltig"

echo "== Bilddatei (PNG) =="
printf 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==' | base64 -d > /tmp/pixel.png
PURL="$(curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=pixel.png" \
  -H "$AUTH" --data-binary @/tmp/pixel.png | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
PTOKEN="$(curl -s -X POST "$BASE/api/entries/$PURL/preview-token" -H "$AUTH" | py 'import sys,json;print(json.load(sys.stdin)["url"])')"
curl -s -o /dev/null -w 'png_preview=%{http_code}\n' "$BASE$PTOKEN"
curl -s -D - -o /dev/null "$BASE$PTOKEN" | grep -iE 'content-type|accept-ranges'

echo "== Aufräumen =="
curl -s -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH" -o /dev/null
curl -s -X DELETE "$BASE/api/trash/$FOLDER" -H "$AUTH" -o /dev/null -w 'purge=%{http_code}\n'
echo VERIFY_PREVIEW_DONE
