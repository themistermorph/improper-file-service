#!/usr/bin/env bash
# Verifiziert Brute-Force-Schutz und Quota-Endpunkt auf dem Testsystem.
set -uo pipefail
BASE="http://127.0.0.1:8000"
CRED=/home/ifs/ifs/ADMIN_CREDENTIALS.txt
USER="$(grep '^username=' "$CRED" | cut -d= -f2)"
PASS="$(grep '^password=' "$CRED" | cut -d= -f2)"
py() { python3 -c "$1"; }
JSON='Content-Type: application/json'

echo "== Login (gültig) =="
TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H "$JSON" \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | py 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
AUTH="Authorization: Bearer $TOKEN"
echo "token_len=${#TOKEN}"

echo "== Brute-Force-Schutz =="
BOGUS="bruteforce-$(date +%s)"
for i in 1 2 3 4 5; do
  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/auth/login" -H "$JSON" \
    -d "{\"username\":\"$BOGUS\",\"password\":\"falsch\"}")"
  echo "Versuch $i -> $code"
done
echo -n "6. Versuch -> "
curl -s -o /dev/null -D - -w '%{http_code}\n' -X POST "$BASE/api/auth/login" -H "$JSON" \
  -d "{\"username\":\"$BOGUS\",\"password\":\"falsch\"}" | grep -iE 'HTTP/|retry-after'

echo "== Gültiger Login trotz Fehlversuchen (anderer Account) =="
curl -s -o /dev/null -w 'admin_login=%{http_code} (erwartet 200)\n' -X POST "$BASE/api/auth/login" \
  -H "$JSON" -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}"

echo "== Quota-Endpunkt =="
curl -s -H "$AUTH" "$BASE/api/quota"; echo

echo VERIFY_RATELIMIT_DONE
