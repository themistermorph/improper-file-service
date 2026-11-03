#!/usr/bin/env bash
# Rollen -> Berechtigungen: phasenweise Migration.
#
# Standard (sicher): 0011 (Schema) + 0012 (Backfill), Rollen bleiben erhalten.
# Cleanup (destruktiv) nur mit ausdruecklicher Freigabe:
#   IFS_PERMISSIONS_CLEANUP=1 bash deploy/apply-permissions.sh
#
# Vorher IMMER PostgreSQL-Dump und S3-Snapshot erstellen.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SERVICE="${IFS_DB_SERVICE:-db}"
DB_USER="${IFS_DB_USER:-ifs}"
DB_NAME="${IFS_DB_NAME:-ifs}"

apply() {
  local file="$1"
  echo "== $(basename "$file") =="
  docker compose exec -T "$SERVICE" \
    psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 < "$file"
}

apply migrations/sql/0011_permissions_schema.sql
apply migrations/sql/0012_permissions_backfill.sql

if [ "${IFS_PERMISSIONS_CLEANUP:-0}" = "1" ]; then
  echo "WARNUNG: Cleanup entfernt alle Rollen ausser 'admin'." >&2
  apply migrations/sql/0013_permissions_cleanup.sql
else
  echo "0013 (Cleanup) uebersprungen. Nach Verifikation: IFS_PERMISSIONS_CLEANUP=1 ..."
fi

echo "PERMISSIONS_MIGRATIONS_OK"
