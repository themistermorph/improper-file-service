#!/usr/bin/env bash
# Diagnose: Client-IP / trusted proxies im Audit-Log.
set -uo pipefail
cd /home/ifs/ifs
echo "== .env (IFS_TRUSTED_PROXIES) =="
grep -E '^IFS_TRUSTED_PROXIES=' .env || echo "(nicht gesetzt)"
echo "== Settings im api-Container =="
docker compose exec -T api python -c "from ifs.config import get_settings; print(repr(get_settings().trusted_proxies))" 2>/dev/null
echo "== Audit-Log: häufigste IPs =="
docker compose exec -T db psql -U ifs -d ifs -A -t -c \
  "select coalesce(ip,'(null)'), count(*) from audit_log group by ip order by count(*) desc limit 12;" 2>/dev/null
echo "== Audit-Log: letzte 8 Einträge (Zeit, Aktion, IP) =="
docker compose exec -T db psql -U ifs -d ifs -A -t -F' | ' -c \
  "select to_char(ts,'YYYY-MM-DD HH24:MI:SS'), action, coalesce(ip,'(null)') from audit_log order by ts desc limit 8;" 2>/dev/null
echo DIAG_CLIENT_IP_DONE
