#!/usr/bin/env bash
# Diagnose des PNG-Vorschau-Schritts.
set -uo pipefail
BASE=http://127.0.0.1:8000
CRED=/home/ifs/ifs/ADMIN_CREDENTIALS.txt
PW="$(grep '^password=' "$CRED" | cut -d= -f2)"
py() { python3 -c "$1"; }
T="$(curl -s -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$PW\"}" | py 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
AUTH="Authorization: Bearer $T"
JSON='Content-Type: application/json'

FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H "$JSON" \
  -d '{"name":"preview-diag"}' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
echo "folder=$FOLDER"

printf 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==' | base64 -d > /tmp/pixel.png
ls -l /tmp/pixel.png

echo "== Upload =="
curl -s -o /tmp/up.json -w 'upload_http=%{http_code}\n' \
  -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=pixel.png" \
  -H "$AUTH" --data-binary @/tmp/pixel.png
cat /tmp/up.json; echo
EID="$(py 'import json;print(json.load(open("/tmp/up.json")).get("id",""))')"
echo "eid=$EID"

echo "== Preview-Token =="
if [ -n "$EID" ]; then
  curl -s -o /tmp/tok.json -w 'token_http=%{http_code}\n' \
    -X POST "$BASE/api/entries/$EID/preview-token" -H "$AUTH"
  cat /tmp/tok.json; echo
  PURL="$(py 'import json;print(json.load(open("/tmp/tok.json")).get("url",""))')"
  echo "purl=$PURL"
  curl -s -o /dev/null -w 'GET_preview=%{http_code}\n' "$BASE$PURL"
  curl -s -D - -o /dev/null "$BASE$PURL" | grep -iE 'HTTP/|content-type|accept-ranges'
fi

curl -s -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH" -o /dev/null
curl -s -X DELETE "$BASE/api/trash/$FOLDER" -H "$AUTH" -o /dev/null
echo DIAG_DONE
