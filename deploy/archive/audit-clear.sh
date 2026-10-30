#!/usr/bin/env bash
# Sichert das Audit-Log und leert es anschließend.
# Backup liegt außerhalb des Repos (/home/ifs/audit-backups), damit der Sync sauber bleibt.
set -euo pipefail
cd /home/ifs/ifs
STAMP="$(date +%F-%H%M%S)"
BACKUP_DIR="/home/ifs/audit-backups"
mkdir -p "$BACKUP_DIR"
BACKUP="${BACKUP_DIR}/audit-${STAMP}.sql"

BEFORE="$(docker compose exec -T db psql -U ifs -d ifs -A -t -c 'select count(*) from audit_log;' | tr -d '[:space:]')"
echo "Eintraege vorher: ${BEFORE}"

docker compose exec -T db pg_dump -U ifs -d ifs -t audit_log > "${BACKUP}"
echo "Backup: ${BACKUP} ($(wc -c < "${BACKUP}") Bytes)"

docker compose exec -T db psql -U ifs -d ifs -c "TRUNCATE TABLE audit_log;"

AFTER="$(docker compose exec -T db psql -U ifs -d ifs -A -t -c 'select count(*) from audit_log;' | tr -d '[:space:]')"
echo "Eintraege nachher: ${AFTER}"
echo AUDIT_CLEAR_DONE
