#!/usr/bin/env bash
# Zeigt die Namespace-Einträge in der Datenbank (als root via sudo).
set -uo pipefail
cd /home/ifs/ifs
docker compose exec -T db psql -U ifs -d ifs -c \
  "SELECT left(id::text,8) AS id, type, name, left(parent_id::text,8) AS parent, trashed_at FROM entries ORDER BY created_at;"
