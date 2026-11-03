#!/usr/bin/env bash
# Prueft nach der Berechtigungs-Migration zentrale Invarianten (read-only).
#
# Aufruf:
#   bash deploy/verify-permissions.sh
set -euo pipefail
cd "${IFS_PROJECT_DIR:-/home/ifs/ifs}"

SERVICE="${IFS_DB_SERVICE:-db}"
DB_USER="${IFS_DB_USER:-ifs}"
DB_NAME="${IFS_DB_NAME:-ifs}"

query() {
  docker compose exec -T "$SERVICE" \
    psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -Atc "$1"
}

echo "-- Rollen (nach Cleanup erwartet: nur admin) --"
query "SELECT name || ':' || permissions FROM roles ORDER BY name;"

echo "-- Fachliche Rollen (nach Cleanup erwartet: 0) --"
query "SELECT count(*) FROM roles WHERE name <> 'admin';"

echo "-- system_permissions --"
query "SELECT principal_type, count(*), coalesce(sum(permissions), 0)
       FROM system_permissions GROUP BY principal_type ORDER BY principal_type;"

echo "-- ACLs ohne gueltige Principals (erwartet: 0) --"
query "SELECT count(*) FROM acl a
       WHERE (a.principal_type = 'user' AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = a.principal_id))
          OR (a.principal_type = 'group' AND NOT EXISTS (SELECT 1 FROM groups g WHERE g.id = a.principal_id));"

echo "-- ACLs mit Systembits (erwartet: 0) --"
query "SELECT count(*) FROM acl WHERE (perms & 192) <> 0;"

echo "-- Entry-gebundene Rollenzuweisungen (nach Cleanup erwartet: 0) --"
query "SELECT count(*) FROM role_assignments WHERE entry_id IS NOT NULL;"

echo "-- users.is_admin ohne Adminrollen-Zuweisung (erwartet: 0) --"
query "SELECT count(*) FROM users u
       WHERE u.is_admin = true
         AND NOT EXISTS (
           SELECT 1 FROM role_assignments ra
           JOIN roles r ON r.id = ra.role_id
           WHERE r.name = 'admin'
             AND ra.principal_type = 'user'
             AND ra.principal_id = u.id
             AND ra.entry_id IS NULL
         );"

echo "VERIFY_PERMISSIONS_DONE"
