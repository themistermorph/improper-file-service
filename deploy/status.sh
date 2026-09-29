#!/usr/bin/env bash
# Status- und Diagnoseabfrage (als root via sudo).
set -uo pipefail
cd /home/ifs/ifs

echo "== Compose-Status =="
docker compose ps

echo "== Container-Logs (letzte Zeilen) =="
for svc in db minio api ftp worker; do
  echo "--- $svc ---"
  docker compose logs --tail=15 "$svc" 2>&1 || true
done

echo "== Health =="
curl -sS -m 5 http://127.0.0.1:8000/healthz || echo "API nicht erreichbar"
echo
curl -sS -m 5 http://127.0.0.1:8000/version || echo "Version nicht erreichbar"
echo
echo "== Admin-Zugangsdaten =="
cat /home/ifs/ifs/ADMIN_CREDENTIALS.txt 2>/dev/null || echo "keine Zugangsdatei"
