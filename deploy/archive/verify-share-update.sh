#!/usr/bin/env bash
# End-to-End-Test: Ändern und Widerrufen von Freigaben.
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

FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H "$JSON" -d '{"name":"share-upd"}' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
EID="$(printf 'inhalt\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=x.txt" -H "$AUTH" --data-binary @- | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
TOK="$(curl -s -X POST "$BASE/api/shares" -H "$AUTH" -H "$JSON" -d "{\"entry_id\":\"$EID\"}" | py 'import sys,json;print(json.load(sys.stdin)["token"])')"

echo "== vorher: öffentlicher Zugriff =="
curl -s -o /dev/null -w 'content=%{http_code}\n' "$BASE/api/shares/$TOK/content"

echo "== Passwort nachträglich setzen =="
curl -s -X PATCH "$BASE/api/shares/$TOK" -H "$AUTH" -H "$JSON" -d '{"password":"geheim"}' \
  | py 'import sys,json;print("has_password:",json.load(sys.stdin)["has_password"])'
curl -s -o /dev/null -w 'ohne_passwort=%{http_code} (erwartet 401)\n' "$BASE/api/shares/$TOK/content"
curl -s -o /dev/null -w 'mit_passwort=%{http_code} (erwartet 200)\n' "$BASE/api/shares/$TOK/content?password=geheim"

echo "== Passwort und Limit ändern =="
curl -s -X PATCH "$BASE/api/shares/$TOK" -H "$AUTH" -H "$JSON" \
  -d '{"password":"","max_downloads":5,"expires_at":"2027-01-01T00:00:00Z"}' \
  | py 'import sys,json;d=json.load(sys.stdin);print("has_password:",d["has_password"],"max:",d["max_downloads"],"expires:",d["expires_at"])'
curl -s -o /dev/null -w 'ohne_passwort_danach=%{http_code} (erwartet 200)\n' "$BASE/api/shares/$TOK/content"

echo "== Widerrufen =="
curl -s -X DELETE "$BASE/api/shares/$TOK" -H "$AUTH" -o /dev/null -w 'revoke=%{http_code}\n'
curl -s -o /dev/null -w 'meta_danach=%{http_code} (erwartet 404)\n' "$BASE/api/shares/$TOK"
curl -s -o /dev/null -w 'content_danach=%{http_code} (erwartet 404)\n' "$BASE/api/shares/$TOK/content"

echo "== Aufräumen =="
curl -s -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH" -o /dev/null
curl -s -X DELETE "$BASE/api/trash/$FOLDER" -H "$AUTH" -o /dev/null
echo VERIFY_SHARE_UPDATE_DONE
