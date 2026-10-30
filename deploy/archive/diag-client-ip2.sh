#!/usr/bin/env bash
# Diagnose 2: Docker-Netz-Subnetz und Audit-IPs zeitlich/Aktion zuordnen.
set -uo pipefail
cd /home/ifs/ifs
echo "== Docker-Netze =="
docker network ls --format '{{.Name}} {{.Driver}}'
for net in $(docker network ls --format '{{.Name}}' | grep -E 'ifs|default'); do
  echo "-- $net"
  docker network inspect "$net" -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null
done
echo "== Audit: pro IP Zeitraum + Top-Aktionen =="
docker compose exec -T db psql -U ifs -d ifs -A -t -F' | ' -c \
  "select coalesce(ip,'(null)'), count(*), to_char(min(ts),'MM-DD HH24:MI'), to_char(max(ts),'MM-DD HH24:MI') from audit_log group by ip order by max(ts) desc;" 2>/dev/null
echo "== Audit: Top (ip, action) =="
docker compose exec -T db psql -U ifs -d ifs -A -t -F' | ' -c \
  "select coalesce(ip,'(null)'), action, count(*) from audit_log group by ip, action order by count(*) desc limit 15;" 2>/dev/null
echo DIAG_CLIENT_IP2_DONE
