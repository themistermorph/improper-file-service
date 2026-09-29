#!/usr/bin/env bash
# Testet automatischen Link-Widerruf beim Löschen und Widerruf aus dem Papierkorb.
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

FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H "$JSON" -d '{"name":"share-trash"}' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"

echo "== 1) Löschen widerruft Link automatisch =="
E1="$(printf 'eins\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=e1.txt" -H "$AUTH" --data-binary @- | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
TOK1="$(curl -s -X POST "$BASE/api/shares" -H "$AUTH" -H "$JSON" -d "{\"entry_id\":\"$E1\"}" | py 'import sys,json;print(json.load(sys.stdin)["token"])')"
curl -s -X DELETE "$BASE/api/entries/$E1" -H "$AUTH" -o /dev/null -w 'entry_loeschen=%{http_code}\n'
curl -s -o /dev/null -w 'link_meta=%{http_code} (erwartet 404)\n' "$BASE/api/shares/$TOK1"
curl -s -o /dev/null -w 'link_content=%{http_code} (erwartet 404)\n' "$BASE/api/shares/$TOK1/content"
if curl -s -H "$AUTH" "$BASE/api/shares" | grep -q "$TOK1"; then
  echo "Link taucht noch in der Übersicht auf: FEHLER"
else
  echo "Link aus Übersicht entfernt: OK"
fi

echo "== 2) Altbestand: Link zu gelöschtem Eintrag widerrufen =="
E2="$(printf 'zwei\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=e2.txt" -H "$AUTH" --data-binary @- | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
TOK2="$(curl -s -X POST "$BASE/api/shares" -H "$AUTH" -H "$JSON" -d "{\"entry_id\":\"$E2\"}" | py 'import sys,json;print(json.load(sys.stdin)["token"])')"
# Eintrag direkt in den Papierkorb setzen (simuliert vor diesem Fix erstellte Links)
docker compose exec -T db psql -U ifs -d ifs -c \
  "UPDATE entries SET trashed_at = now() WHERE id = '$E2';" >/dev/null
echo "Link in Übersicht (trotz Papierkorb): $(curl -s -H "$AUTH" "$BASE/api/shares" | grep -q "$TOK2" && echo ja || echo nein)"
curl -s -X DELETE "$BASE/api/shares/$TOK2" -H "$AUTH" -o /dev/null -w 'widerruf_papierkorb=%{http_code} (erwartet 204)\n'
curl -s -o /dev/null -w 'meta_danach=%{http_code} (erwartet 404)\n' "$BASE/api/shares/$TOK2"

echo "== Aufräumen =="
curl -s -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH" -o /dev/null
curl -s -X DELETE "$BASE/api/trash/$FOLDER" -H "$AUTH" -o /dev/null
echo VERIFY_SHARE_TRASH_DONE
