#!/usr/bin/env bash
# Prüft auf Duplikate und legt den funktionalen Unique-Index an (Ausbaustufe 2, N3).
set -uo pipefail
cd /home/ifs/ifs
PSQL="docker compose exec -T db psql -U ifs -d ifs"

echo "== Duplikate (leer = ok) =="
$PSQL -c "SELECT parent_id, lower(name), count(*) FROM entries WHERE trashed_at IS NULL GROUP BY 1,2 HAVING count(*) > 1;"

echo "== Migration 0004 =="
if $PSQL -v ON_ERROR_STOP=1 < migrations/sql/0004_unique_entry_names.sql; then
  echo MIGRATION_OK
else
  echo MIGRATION_FAIL
fi
$PSQL -c "\di" | grep -i uq_entries || true
