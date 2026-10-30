#!/usr/bin/env bash
# Verifiziert den FTPS-Audit-Rollout: Version/Health, FTPS-Smoke, Audit-Einträge (protocol=ftps).
set -uo pipefail
cd /home/ifs/ifs
echo "== version/health =="
curl -sS -m5 http://127.0.0.1:8000/version; echo
curl -sS -m5 http://127.0.0.1:8000/healthz; echo
echo "== FTP-Smoke (login/list/upload/download) =="
bash deploy/ftp-check.sh 2>&1 | tail -n 3
echo "== Audit-Einträge protocol=ftps (letzte 12) =="
docker compose exec -T db psql -U ifs -d ifs -A -t -F' | ' -c \
  "select to_char(ts,'HH24:MI:SS'), action, result, coalesce(ip,'-') from audit_log where protocol='ftps' order by ts desc limit 12;"
echo VERIFY_FTPS_AUDIT_DONE
