#!/usr/bin/env bash
# Read-only: Migrationsstand der Server-Datenbank prüfen.
set -uo pipefail
cd /home/ifs/ifs
PSQL="docker compose exec -T db psql -U ifs -d ifs -A -t"
echo "== alembic_version =="
$PSQL -c "select to_regclass('public.alembic_version');" 2>/dev/null
$PSQL -c "select version_num from alembic_version;" 2>/dev/null || echo "  (keine alembic_version-Tabelle)"
echo "== Spalten entries =="
$PSQL -c "select column_name from information_schema.columns where table_name='entries' order by column_name;"
echo "== Spalten shares =="
$PSQL -c "select column_name from information_schema.columns where table_name='shares' order by column_name;"
echo "== Spalten users =="
$PSQL -c "select column_name from information_schema.columns where table_name='users' order by column_name;"
echo "== Indizes entries =="
$PSQL -c "select indexname from pg_indexes where tablename='entries' order by indexname;"
echo "== Wurzel-Index vorhanden? =="
$PSQL -c "select count(*) from pg_indexes where indexname='uq_entries_root_owner';"
echo "== Wurzeln je Benutzer (soll je aktive Nutzer 1 sein) =="
$PSQL -c "select u.username, count(e.id) as roots from users u left join entries e on e.owner_id=u.id and e.parent_id is null and e.trashed_at is null group by u.username order by u.username;"
echo MIGRATION_STATE_DONE
