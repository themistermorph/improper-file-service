#!/usr/bin/env bash
# Wendet alle SQL-Migrationen aus migrations/sql/ in Dateinamen-Reihenfolge an.
# Die Migrationen sind idempotent (IF NOT EXISTS) und können erneut laufen.
#
# Aufruf auf dem Deploy-Host:
#   bash deploy/apply-migrations.sh
#
# Optional anpassbar:
#   IFS_DB_SERVICE=db IFS_DB_USER=ifs IFS_DB_NAME=ifs bash deploy/apply-migrations.sh
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SERVICE="${IFS_DB_SERVICE:-db}"
DB_USER="${IFS_DB_USER:-ifs}"
DB_NAME="${IFS_DB_NAME:-ifs}"

shopt -s nullglob
files=(migrations/sql/*.sql)
if [ "${#files[@]}" -eq 0 ]; then
  echo "FEHLER: Keine Migrationen unter migrations/sql/ gefunden." >&2
  exit 1
fi

for file in "${files[@]}"; do
  base="$(basename "$file")"
  # Destruktiver Cleanup nur nach ausdruecklicher Freigabe. Die additiven
  # Schritte 0011/0012 laufen normal mit.
  if [ "$base" = "0013_permissions_cleanup.sql" ] \
     && [ "${IFS_PERMISSIONS_CLEANUP:-0}" != "1" ]; then
    echo "== $base uebersprungen (Cleanup: IFS_PERMISSIONS_CLEANUP=1) =="
    continue
  fi
  echo "== $base =="
  docker compose exec -T "$SERVICE" \
    psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 < "$file"
done

echo "MIGRATIONS_OK (${#files[@]} Dateien)"
