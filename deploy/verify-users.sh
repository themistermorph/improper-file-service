#!/usr/bin/env bash
# End-to-End-Test der Kontoverwaltung gegen die lokal laufende API.
set -uo pipefail
BASE="${1:-http://127.0.0.1:8000}"
CRED=/home/ifs/ifs/ADMIN_CREDENTIALS.txt
ADMIN_USER="$(grep '^username=' "$CRED" | cut -d= -f2)"
ADMIN_PW="$(grep '^password=' "$CRED" | cut -d= -f2)"
JSON='Content-Type: application/json'

TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H "$JSON" \
  -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PW\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
AUTH="Authorization: Bearer $TOKEN"

U="acct-$(date +%s)"
echo "== Benutzer anlegen =="
U_ID="$(curl -s -X POST "$BASE/api/users" -H "$AUTH" -H "$JSON" \
  -d "{\"username\":\"$U\",\"password\":\"start1234\",\"email\":\"$U@example.org\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')"
echo "id=$U_ID"

echo "== Suche =="
curl -s -H "$AUTH" "$BASE/api/users?q=$U" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("total:",d["total"],"items:",[(u["username"],u["is_admin"]) for u in d["items"]])'

echo "== Bearbeiten =="
curl -s -X PATCH "$BASE/api/users/$U_ID" -H "$AUTH" -H "$JSON" \
  -d '{"display_name":"Testkonto"}' \
  | python3 -c 'import sys,json;print("display:",json.load(sys.stdin)["display_name"])'

echo "== Deaktivieren -> Login scheitert =="
curl -s -X POST "$BASE/api/users/$U_ID/deactivate" -H "$AUTH" -o /dev/null -w 'deactivate=%{http_code}\n'
curl -s -o /dev/null -w 'login_deaktiviert=%{http_code}\n' -X POST "$BASE/api/auth/login" \
  -H "$JSON" -d "{\"username\":\"$U\",\"password\":\"start1234\"}"
curl -s -X POST "$BASE/api/users/$U_ID/activate" -H "$AUTH" -o /dev/null -w 'activate=%{http_code}\n'

echo "== Passwort-Reset (Admin) =="
curl -s -X POST "$BASE/api/users/$U_ID/password" -H "$AUTH" -H "$JSON" \
  -d '{"password":"reset12345"}' -o /dev/null -w 'reset=%{http_code}\n'
U_TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H "$JSON" \
  -d "{\"username\":\"$U\",\"password\":\"reset12345\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
echo "login_nach_reset=ok"

echo "== Selbstbedienung: eigenes Passwort ändern =="
curl -s -X POST "$BASE/api/me/password" -H "Authorization: Bearer $U_TOKEN" -H "$JSON" \
  -d '{"current_password":"reset12345","new_password":"final12345"}' \
  -o /dev/null -w 'change=%{http_code}\n'
curl -s -o /dev/null -w 'alter_token=%{http_code}\n' \
  -H "Authorization: Bearer $U_TOKEN" "$BASE/api/me"
curl -s -o /dev/null -w 'login_neu=%{http_code}\n' -X POST "$BASE/api/auth/login" -H "$JSON" \
  -d "{\"username\":\"$U\",\"password\":\"final12345\"}"

echo "== Letzter-Admin-Schutz =="
ADMIN_ID="$(curl -s -H "$AUTH" "$BASE/api/users?q=$ADMIN_USER" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["items"][0]["id"])')"
curl -s -o /dev/null -w 'deactivate_admin=%{http_code} (erwartet 409)\n' \
  -X POST "$BASE/api/users/$ADMIN_ID/deactivate" -H "$AUTH"

echo "== Aufräumen =="
curl -s -X DELETE "$BASE/api/users/$U_ID" -H "$AUTH" -o /dev/null -w 'delete=%{http_code}\n'
echo VERIFY_USERS_DONE
