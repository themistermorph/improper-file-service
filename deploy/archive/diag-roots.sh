#!/usr/bin/env bash
# Read-only: aktuelle Wurzel-/Besitz-Struktur auf dem Testsystem.
set -uo pipefail
cd /home/ifs/ifs
echo "== Benutzer =="
docker compose exec -T db psql -U ifs -d ifs -At -c \
  "select 'users=' || count(*) || ' active=' || count(*) filter (where is_active) || ' admins=' || count(*) filter (where is_admin) from users;"
echo "== Wurzeln (parent_id IS NULL) =="
docker compose exec -T db psql -U ifs -d ifs -c \
  "select id, name, owner_id, trashed_at from entries where parent_id is null order by created_at;"
echo "== Einträge je Besitzer =="
docker compose exec -T db psql -U ifs -d ifs -c \
  "select u.username, count(e.id) as entries, count(e.id) filter (where e.parent_id is null) as roots
   from users u left join entries e on e.owner_id = u.id group by u.username order by entries desc;"
echo "== Top-Level-Einträge (aktiv, unter der Wurzel) =="
docker compose exec -T db psql -U ifs -d ifs -c \
  "select e.name, u.username as owner from entries e join users u on u.id=e.owner_id
   where e.parent_id = (select id from entries where parent_id is null order by created_at limit 1)
   order by e.name;"
echo DIAG_ROOTS_DONE
