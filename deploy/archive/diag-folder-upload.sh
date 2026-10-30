#!/usr/bin/env bash
set -uo pipefail
cd /home/ifs/ifs
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
BASE="http://127.0.0.1:8000"
TOKEN="$(curl -fsS -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"
AUTH="Authorization: Bearer $TOKEN"
curl -fsS -H "$AUTH" "$BASE/api/audit?limit=100" -o /tmp/ifs-audit.json

echo "== Letzte Upload-/Entry-Audit-Einträge =="
python3 -c '
import json
data = json.load(open("/tmp/ifs-audit.json"))
items = data if isinstance(data, list) else data.get("items", [])
for item in items:
    action = item.get("action", "")
    if "upload" in action or "entry" in action:
        print(item.get("ts"), action, item.get("protocol"), item.get("ip"),
              item.get("result"), item.get("details"))
'

echo
echo "== API-Log (letzte 2h, Upload/Fehler) =="
docker compose logs --since=2h --no-color api 2>/dev/null | \
  grep -iE "upload|error|traceback|exception|422|400|409|500" | tail -n 80 || true
