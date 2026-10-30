#!/usr/bin/env bash
# Entfernt den während der Diagnose angelegten Benutzer "diaguser".
set -uo pipefail
cd /home/ifs/ifs
PW="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
T="$(curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$PW\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
ID="$(curl -s -H "Authorization: Bearer $T" "http://127.0.0.1:8000/api/users?q=diaguser" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["items"][0]["id"] if d["items"] else "")')"
if [ -n "$ID" ]; then
  curl -s -X DELETE "http://127.0.0.1:8000/api/users/$ID" -H "Authorization: Bearer $T" \
    -o /dev/null -w 'delete_diaguser=%{http_code}\n'
else
  echo "diaguser nicht vorhanden"
fi
