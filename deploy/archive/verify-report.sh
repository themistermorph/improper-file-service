#!/usr/bin/env bash
# Live-Verifikation der Report-Findings H1, M1, M2, N1.
set -uo pipefail
BASE="http://127.0.0.1:8000"
CRED=/home/ifs/ifs/ADMIN_CREDENTIALS.txt
USER="$(grep '^username=' "$CRED" | cut -d= -f2)"
PASS="$(grep '^password=' "$CRED" | cut -d= -f2)"
py() { python3 -c "$1"; }
JSON='Content-Type: application/json'
login() { curl -s -X POST "$BASE/api/auth/login" -H "$JSON" -d "{\"username\":\"$1\",\"password\":\"$2\"}" | py 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }

TOKEN="$(login "$USER" "$PASS")"
AUTH="Authorization: Bearer $TOKEN"

echo "== H1: Wiederherstellung nur autorisiert =="
BOB="h1bob-$(date +%s)"
BOB_ID="$(curl -s -X POST "$BASE/api/users" -H "$AUTH" -H "$JSON" -d "{\"username\":\"$BOB\",\"password\":\"geheim123\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
BOB_AUTH="Authorization: Bearer $(login "$BOB" geheim123)"
FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H "$JSON" -d '{"name":"h1folder"}' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
curl -s -X POST "$BASE/api/entries/$FOLDER/acl" -H "$AUTH" -H "$JSON" \
  -d "{\"principal_type\":\"user\",\"principal_id\":\"$BOB_ID\",\"perms\":[\"read\",\"write\"]}" -o /dev/null
FID="$(printf 'x\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=f.txt" -H "$BOB_AUTH" --data-binary @- | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
curl -s -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH" -o /dev/null
curl -s -o /dev/null -w 'bob_restore_nested=%{http_code} (erwartet 403)\n' -X POST "$BASE/api/trash/$FID/restore" -H "$BOB_AUTH" -H "$JSON" -d '{}'
curl -s -o /dev/null -w 'bob_restore_folder=%{http_code} (erwartet 403)\n' -X POST "$BASE/api/trash/$FOLDER/restore" -H "$BOB_AUTH" -H "$JSON" -d '{}'
curl -s -o /dev/null -w 'admin_restore_folder=%{http_code} (erwartet 200)\n' -X POST "$BASE/api/trash/$FOLDER/restore" -H "$AUTH" -H "$JSON" -d '{}'

echo "== M1: aktive Inhalte als Attachment =="
HID="$(printf '<html><script>alert(1)</script></html>\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=page.html" -H "$AUTH" --data-binary @- | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
curl -s -D - -o /dev/null -H "$AUTH" "$BASE/api/entries/$HID/content" \
  | grep -iE 'content-type|content-disposition|content-security-policy' | sed 's/:.*/: <gesetzt>/;s/content-security-policy/content-security-policy/'

echo "== M2: Freigabe-Passwort per Header + Limit =="
SHARE="$(curl -s -X POST "$BASE/api/shares" -H "$AUTH" -H "$JSON" -d "{\"entry_id\":\"$HID\",\"password\":\"pw\",\"max_downloads\":1}")"
STOK="$(echo "$SHARE" | py 'import sys,json;print(json.load(sys.stdin)["token"])')"
curl -s -o /dev/null -w 'ohne_passwort=%{http_code} (erwartet 401)\n' "$BASE/api/shares/$STOK/content"
curl -s -o /dev/null -w 'mit_passwort_header=%{http_code} (erwartet 200)\n' -H 'X-Share-Password: pw' "$BASE/api/shares/$STOK/content"
curl -s -o /dev/null -w 'limit_erreicht=%{http_code} (erwartet 410)\n' -H 'X-Share-Password: pw' "$BASE/api/shares/$STOK/content"

echo "== N1: scoped Token nach Passwortänderung ungültig =="
PURL="$(curl -s -X POST "$BASE/api/entries/$HID/preview-token" -H "$AUTH" | py 'import sys,json;print(json.load(sys.stdin)["url"])')"
curl -s -o /dev/null -w 'preview_vorher=%{http_code} (erwartet 200)\n' "$BASE$PURL"
NEWPW="pw-$(date +%s)"
curl -s -X POST "$BASE/api/me/password" -H "$AUTH" -H "$JSON" -d "{\"current_password\":\"$PASS\",\"new_password\":\"$NEWPW\"}" -o /dev/null -w 'passwort_aendern=%{http_code}\n'
curl -s -o /dev/null -w 'preview_nachher=%{http_code} (erwartet 401)\n' "$BASE$PURL"
# Passwort zurücksetzen, damit Zugangsdaten stabil bleiben
NEWTOKEN="$(login "$USER" "$NEWPW")"
curl -s -X POST "$BASE/api/me/password" -H "Authorization: Bearer $NEWTOKEN" -H "$JSON" -d "{\"current_password\":\"$NEWPW\",\"new_password\":\"$PASS\"}" -o /dev/null

echo "== Aufräumen =="
AUTH2="Authorization: Bearer $(login "$USER" "$PASS")"
curl -s -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH2" -o /dev/null
curl -s -X DELETE "$BASE/api/trash/$FOLDER" -H "$AUTH2" -o /dev/null
curl -s -X DELETE "$BASE/api/users/$BOB_ID" -H "$AUTH2" -o /dev/null
echo VERIFY_REPORT_DONE
