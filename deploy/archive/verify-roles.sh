#!/usr/bin/env bash
# End-to-End-Test des Rollenmanagements gegen die lokal laufende API.
set -uo pipefail
BASE="${1:-http://127.0.0.1:8000}"
CRED=/home/ifs/ifs/ADMIN_CREDENTIALS.txt
ADMIN_USER="$(grep '^username=' "$CRED" | cut -d= -f2)"
ADMIN_PW="$(grep '^password=' "$CRED" | cut -d= -f2)"
JSON='Content-Type: application/json'
py() { python3 -c "$1"; }

TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H "$JSON" \
  -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PW\"}" | py 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
AUTH="Authorization: Bearer $TOKEN"

echo "== Rollen (Auszug) =="
curl -s -H "$AUTH" "$BASE/api/roles" \
  | py 'import sys,json;d=json.load(sys.stdin);print(sorted(r["name"] for r in d))'

echo "== Eigene Ressourcenrolle anlegen =="
ROLE_ID="$(curl -s -X POST "$BASE/api/roles" -H "$AUTH" -H "$JSON" \
  -d '{"name":"reviewer","scope":"resource","permissions":["read","write"]}' \
  | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
echo "role=$ROLE_ID"

echo "== Benutzer + Ordner =="
U="rollo-$(date +%s)"
U_ID="$(curl -s -X POST "$BASE/api/users" -H "$AUTH" -H "$JSON" \
  -d "{\"username\":\"$U\",\"password\":\"geheim123\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H "$JSON" \
  -d '{"name":"rollen-test"}' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
echo "user=$U_ID folder=$FOLDER"

echo "== Ressourcenrolle zuweisen =="
ASSIGN="$(curl -s -X POST "$BASE/api/entries/$FOLDER/roles" -H "$AUTH" -H "$JSON" \
  -d "{\"role_id\":\"$ROLE_ID\",\"principal_type\":\"user\",\"principal_id\":\"$U_ID\"}" \
  | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
echo "assignment=$ASSIGN"

U_TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H "$JSON" \
  -d "{\"username\":\"$U\",\"password\":\"geheim123\"}" | py 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
UA="Authorization: Bearer $U_TOKEN"
curl -s -o /dev/null -w 'lesen=%{http_code}\n' -H "$UA" "$BASE/api/entries/$FOLDER"
curl -s -o /dev/null -w 'unterordner_anlegen=%{http_code}\n' -X POST "$BASE/api/folders" \
  -H "$UA" -H "$JSON" -d "{\"parent_id\":\"$FOLDER\",\"name\":\"sub\"}"

echo "== Zuweisung entfernen -> kein Zugriff =="
curl -s -o /dev/null -w 'unassign=%{http_code}\n' -X DELETE "$BASE/api/entries/$FOLDER/roles/$ASSIGN" -H "$AUTH"
curl -s -o /dev/null -w 'lesen_danach=%{http_code} (erwartet 403)\n' -H "$UA" "$BASE/api/entries/$FOLDER"

echo "== Systemrolle admin =="
SYS_ADMIN="$(curl -s -H "$AUTH" "$BASE/api/roles?scope=system" \
  | py 'import sys,json;print(next(r["id"] for r in json.load(sys.stdin) if r["name"]=="admin"))')"
curl -s -o /dev/null -w 'admin_endpoint_vorher=%{http_code} (erwartet 403)\n' -H "$UA" "$BASE/api/users"
SASSIGN="$(curl -s -X POST "$BASE/api/users/$U_ID/roles/$SYS_ADMIN" -H "$AUTH" \
  | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
curl -s -o /dev/null -w 'admin_endpoint_nachher=%{http_code} (erwartet 200)\n' -H "$UA" "$BASE/api/users"
curl -s -o /dev/null -w 'systemrolle_entfernen=%{http_code}\n' \
  -X DELETE "$BASE/api/users/$U_ID/roles/$SASSIGN" -H "$AUTH"

echo "== Eingebaute Rolle schützen =="
MANAGER="$(curl -s -H "$AUTH" "$BASE/api/roles" | py 'import sys,json;print(next(r["id"] for r in json.load(sys.stdin) if r["name"]=="manager"))')"
curl -s -o /dev/null -w 'builtin_loeschen=%{http_code} (erwartet 409)\n' -X DELETE "$BASE/api/roles/$MANAGER" -H "$AUTH"

echo "== Aufräumen =="
curl -s -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH" -o /dev/null
curl -s -X DELETE "$BASE/api/trash/$FOLDER" -H "$AUTH" -o /dev/null
curl -s -X DELETE "$BASE/api/users/$U_ID" -H "$AUTH" -o /dev/null -w 'user_loeschen=%{http_code}\n'
curl -s -X DELETE "$BASE/api/roles/$ROLE_ID" -H "$AUTH" -o /dev/null -w 'role_loeschen=%{http_code}\n'
echo VERIFY_ROLES_DONE
