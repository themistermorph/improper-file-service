#!/usr/bin/env bash
# End-to-End-Test der öffentlichen Veröffentlichungen (Galerie /published):
# Datei und Ordner (ZIP) veröffentlichen, öffentlich laden, zurückziehen.
set -uo pipefail

BASE="${1:-http://127.0.0.1:8000}"
CRED=/home/ifs/ifs/ADMIN_CREDENTIALS.txt

if [ ! -f "$CRED" ]; then
  echo "Zugangsdatei fehlt: $CRED"
  exit 1
fi
USER="$(grep '^username=' "$CRED" | cut -d= -f2)"
PASS="$(grep '^password=' "$CRED" | cut -d= -f2)"

echo "== health =="
curl -sS -m 10 "$BASE/healthz"; echo

TOKEN="$(curl -sS -m 10 -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("access_token",""))')"
if [ -z "$TOKEN" ]; then
  echo "LOGIN_FAIL"
  exit 1
fi
echo "LOGIN_OK (user=$USER)"
AUTH="Authorization: Bearer $TOKEN"
CT='Content-Type: application/json'

NAME="pub-$(date +%s)"
FOLDER="$(curl -sS -m 10 -X POST "$BASE/api/folders" -H "$AUTH" -H "$CT" \
  -d "{\"name\":\"$NAME\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("id",""))')"
echo "Ordner: $NAME -> $FOLDER"

printf 'published via verify-published\n' > /tmp/ifs-published.txt
UP="$(curl -sS -m 20 -X PUT \
  "$BASE/api/uploads/simple?parent_id=$FOLDER&name=pub.txt" \
  -H "$AUTH" --data-binary @/tmp/ifs-published.txt)"
EID="$(echo "$UP" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("id",""))')"
echo "Upload: $EID"

echo "== Datei veröffentlichen =="
curl -sS -m 10 -X POST "$BASE/api/published/$EID" -H "$AUTH"; echo
curl -sS -m 10 "$BASE/api/published" | grep -q "$EID" || { echo "GALERIE_FEHLT"; exit 1; }

echo "== öffentlicher Download (Datei, ohne Login) =="
DL="$(curl -sS -m 10 "$BASE/api/published/$EID/content")"
echo "$DL"
[ "$DL" = "published via verify-published" ] || { echo "DOWNLOAD_FALSCH"; exit 1; }

echo "== Galerie-Seite =="
curl -sS -m 10 -o /dev/null -w 'GET /published -> HTTP %{http_code}\n' "$BASE/published"

echo "== Datei zurückziehen =="
curl -sS -m 10 -o /dev/null -w 'DELETE -> HTTP %{http_code}\n' -X DELETE "$BASE/api/published/$EID" -H "$AUTH"
curl -sS -m 10 "$BASE/api/published" | grep -q "$EID" && { echo "ZURUECKZIEHEN_FEHLGESCHLAGEN"; exit 1; }
curl -sS -m 10 -o /dev/null -w 'Download nach Widerruf -> HTTP %{http_code}\n' "$BASE/api/published/$EID/content"

echo "== Ordner veröffentlichen (ZIP) =="
curl -sS -m 10 -X POST "$BASE/api/published/$FOLDER" -H "$AUTH"; echo
curl -sS -m 10 "$BASE/api/published" | grep -q "$FOLDER" || { echo "ORDNER_GALERIE_FEHLT"; exit 1; }
curl -sS -m 20 -o /tmp/ifs-published.zip -w 'ZIP-Download -> HTTP %{http_code}\n' \
  "$BASE/api/published/$FOLDER/content"
python3 - <<'PY' || { echo "ZIP_INHALT_FALSCH"; exit 1; }
import zipfile
with zipfile.ZipFile("/tmp/ifs-published.zip") as zf:
    names = zf.namelist()
assert any(n.endswith("pub.txt") for n in names), names
print("ZIP-Inhalt ok:", names)
PY

echo "== Ordner zurückziehen =="
curl -sS -m 10 -o /dev/null -w 'DELETE -> HTTP %{http_code}\n' -X DELETE "$BASE/api/published/$FOLDER" -H "$AUTH"
curl -sS -m 10 "$BASE/api/published" | grep -q "$FOLDER" && { echo "ORDNER_WIDERSPRUCH_FEHLGESCHLAGEN"; exit 1; }

echo "== Aufräumen =="
curl -sS -m 10 -o /dev/null -w 'Testordner löschen -> HTTP %{http_code}\n' \
  -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH"

echo "VERIFY_PUBLISHED_DONE"
