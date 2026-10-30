#!/usr/bin/env bash
# Verifiziert das Feature „Veröffentlichungen“ (Migration 0008 / v0.1.3).
set -uo pipefail
cd /home/ifs/ifs
BASE="http://127.0.0.1:8000"
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
py() { python3 -c "$1"; }
jget() { python3 -c "import sys,json;print(json.load(sys.stdin)$1)"; }
J='Content-Type: application/json'
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H "$J" -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | jget "['access_token']")"
AUTH="Authorization: Bearer $TOKEN"
ROOT="$(curl -s -H "$AUTH" "$BASE/api/root" | jget "['id']")"
NAME="pub-test-$$.txt"
FILE="$(printf 'oeffentlich\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$ROOT&name=$NAME" -H "$AUTH" --data-binary @- | jget "['id']")"

echo "veroeffentlichen:        $(code -X POST "$BASE/api/published/$FILE" -H "$AUTH") (erwartet 201)"
echo "oeffentliche Liste:      $(curl -s "$BASE/api/published" | py "import sys,json;d=json.load(sys.stdin);print([e['name'] for e in d])")"
echo "oeff. Download:          $(code "$BASE/api/published/$FILE/content") (erwartet 200)"
echo "Galerie-HTML /published: $(code "$BASE/published") (erwartet 200)"
echo "Wurzel veroeffentlichen: $(code -X POST "$BASE/api/published/$ROOT" -H "$AUTH") (erwartet 400)"
echo "eigene Liste:            $(code "$BASE/api/published/mine" -H "$AUTH") (erwartet 200)"
echo "zurueckziehen:           $(code -X DELETE "$BASE/api/published/$FILE" -H "$AUTH") (erwartet 204)"
echo "danach Liste leer:       $(curl -s "$BASE/api/published" | py "import sys,json;print(len(json.load(sys.stdin)))") (erwartet 0)"

echo "== health/version =="
curl -sS -m5 "$BASE/healthz"; echo
curl -sS -m5 "$BASE/version"; echo

echo "== Aufräumen =="
curl -s -o /dev/null -X DELETE "$BASE/api/entries/$FILE" -H "$AUTH"
curl -s -o /dev/null -X DELETE "$BASE/api/trash/$FILE" -H "$AUTH"
echo VERIFY_PUBLISHED_DONE
