#!/usr/bin/env bash
# End-to-End-Sicherheitstest: JWT-Zweck, Header-Injection, Defaults.
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

FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H "$JSON" -d '{"name":"sec-test"}' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
EID="$(printf 'geheim\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=f.txt" -H "$AUTH" --data-binary @- | py 'import sys,json;print(json.load(sys.stdin)["id"])')"

echo "== 1) JWT-Zweck =="
curl -s -o /dev/null -w 'access_token=/api/me -> %{http_code} (erwartet 200)\n' -H "$AUTH" "$BASE/api/me"
PT="$(curl -s -X POST "$BASE/api/entries/$EID/preview-token" -H "$AUTH" | py 'import sys,json;print(json.load(sys.stdin)["token"])')"
AT="$(curl -s -X POST "$BASE/api/entries/$EID/archive-token" -H "$AUTH" | py 'import sys,json;print(json.load(sys.stdin)["token"])')"
curl -s -o /dev/null -w 'preview_token=/api/me -> %{http_code} (erwartet 401)\n' -H "Authorization: Bearer $PT" "$BASE/api/me"
curl -s -o /dev/null -w 'preview_token=/api/entries -> %{http_code} (erwartet 401)\n' -H "Authorization: Bearer $PT" "$BASE/api/entries"
curl -s -o /dev/null -w 'archive_token=/api/me -> %{http_code} (erwartet 401)\n' -H "Authorization: Bearer $AT" "$BASE/api/me"
PURL="$(curl -s -X POST "$BASE/api/entries/$EID/preview-token" -H "$AUTH" | py 'import sys,json;print(json.load(sys.stdin)["url"])')"
curl -s -o /dev/null -w 'preview_url (ohne Bearer) -> %{http_code} (erwartet 200)\n' "$BASE$PURL"

echo "== 2) Header-Injection =="
curl -s -o /dev/null -w 'name_mit_newline -> %{http_code} (erwartet 400)\n' \
  -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=bad%0Aname.txt" \
  -H "$AUTH" --data-binary 'x'
INJ="$(printf 'hallo\n' | curl -s -X PUT \
  "$BASE/api/uploads/simple?parent_id=$FOLDER&name=note.txt&mime=text/plain%0D%0AX-Injected:%20yes" \
  -H "$AUTH" --data-binary @- | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
echo "gespeicherter MIME: $(curl -s -H "$AUTH" "$BASE/api/entries/$INJ" | py 'import sys,json;print(json.load(sys.stdin)["mime"])')"
curl -s -D - -o /dev/null -H "$AUTH" "$BASE/api/entries/$INJ/content?download=true" \
  | grep -iE 'content-type|content-disposition|x-injected' || true

echo "== Name mit Anführungszeichen =="
QID="$(printf 'ok\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=re%22port.txt" \
  -H "$AUTH" --data-binary @- | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
echo "gespeicherter Name: $(curl -s -H "$AUTH" "$BASE/api/entries/$QID" | py 'import sys,json;print(json.load(sys.stdin)["name"])')"
curl -s -D - -o /dev/null -H "$AUTH" "$BASE/api/entries/$QID/content?download=true" \
  | grep -i 'content-disposition' || true

echo "== Aufräumen =="
curl -s -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH" -o /dev/null
curl -s -X DELETE "$BASE/api/trash/$FOLDER" -H "$AUTH" -o /dev/null
echo VERIFY_SECURITY_DONE
