#!/usr/bin/env bash
# Diagnose der Benutzer-Endpunkte im api-Container (als root via sudo).
set -uo pipefail
cd /home/ifs/ifs

echo "== registrierte users-Routen (Modul) =="
docker compose exec -T api python -c "
import ifs.api.users as u
print('file:', u.__file__)
for r in u.router.routes:
    print(sorted(getattr(r, 'methods', [])), r.path)
"

echo "== OpenAPI-Pfade mit 'user' oder /me =="
docker compose exec -T api python -c "
from ifs.main import app
paths = app.openapi()['paths']
for p in sorted(paths):
    if 'users' in p or p == '/api/me':
        print(p, sorted(paths[p].keys()))
"

echo "== Login + POST /api/users (Status/Body) =="
PW="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
T="$(curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$PW\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
echo "token_len=${#T}"
curl -s -o /tmp/user-create.json -w 'POST /api/users -> %{http_code}\n' \
  -X POST http://127.0.0.1:8000/api/users -H "Authorization: Bearer $T" \
  -H 'Content-Type: application/json' \
  -d '{"username":"diaguser","password":"diag12345"}'
cat /tmp/user-create.json; echo
