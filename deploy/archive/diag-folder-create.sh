#!/usr/bin/env bash
set -uo pipefail
cd /home/ifs/ifs
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
BASE=http://127.0.0.1:8000
T="$(curl -fsS -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')"
echo "ROOT:"
curl -sS -H "Authorization: Bearer $T" "$BASE/api/root"; echo
echo "FOLDER:"
curl -sS -X POST -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -d '{"name":"diag-folder-test-'"$(date +%s)"'"}' \
  -w '\nHTTP %{http_code}\n' "$BASE/api/folders"
echo "LOG:"
docker compose logs --since=10m --no-color api | tail -n 40
