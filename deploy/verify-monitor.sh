#!/usr/bin/env bash
# Verifiziert das Admin-Monitoring (/api/system/stats) und die UI-Marker.
set -uo pipefail
cd /home/ifs/ifs
BASE="http://127.0.0.1:8000"
CRED=ADMIN_CREDENTIALS.txt
USER="$(grep '^username=' "$CRED" | cut -d= -f2)"
PASS="$(grep '^password=' "$CRED" | cut -d= -f2)"
py() { python3 -c "$1"; }

TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | py 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
AUTH="Authorization: Bearer $TOKEN"

echo "== Erste Abfrage =="
curl -s -H "$AUTH" "$BASE/api/system/stats" | py '
import sys, json
d = json.load(sys.stdin)
print("cpu%:", d["cpu"]["percent"], "| cores:", d["cpu"]["cores"], "| load:", d["cpu"]["load_1"])
print("ram:", d["memory"])
print("disk:", d["disk"])
print("net:", d["network"])
print("ifs:", d["ifs"], "| uptime(s):", d["uptime_seconds"])
print("seaweedfs:", d["seaweedfs"])'

echo "== Zweite Abfrage nach 3 s (Raten) =="
sleep 3
curl -s -H "$AUTH" "$BASE/api/system/stats" | py '
import sys, json
d = json.load(sys.stdin)
print("cpu%:", d["cpu"]["percent"])
print("net rx/tx B/s:", d["network"]["rx_bytes_per_sec"], "/", d["network"]["tx_bytes_per_sec"])'

echo "== UI-Marker (Anzahl) =="
curl -s "$BASE/" | grep -c -E 'monitor-interval|sparkline|pushSeries|chartCard'
echo VERIFY_MONITOR_DONE
