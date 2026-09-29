#!/usr/bin/env bash
# Diagnose: UI-Auslieferung und /api/root.
set -uo pipefail
cd /home/ifs/ifs
PW="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"

echo "== GET / (Header) =="
curl -sS -m 5 -i http://127.0.0.1:8000/ | head -n 6
echo
echo "== GET /api/root =="
TOKEN="$(curl -sS -m 5 -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$PW\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
curl -sS -m 5 -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/root; echo

echo "== web-Verzeichnis im api-Container =="
docker compose exec -T api sh -lc '
  python -c "import ifs, pathlib; p=pathlib.Path(ifs.__file__).parent/\"web\"; print(\"web_dir=\", p, \"exists=\", p.exists())";
  ls -la "$(python -c "import ifs,pathlib;print(pathlib.Path(ifs.__file__).parent/\"web\")")" 2>&1 | head
'
