#!/usr/bin/env bash
# Prüft Migrations-/Feature-Stand „Veröffentlichungen“ (0008).
set -uo pipefail
cd /home/ifs/ifs
echo "== migrations/sql =="
ls -1 migrations/sql
echo "== published_entries vorhanden? =="
docker compose exec -T db psql -U ifs -d ifs -A -t -c \
  "select coalesce(to_regclass('public.published_entries')::text, '(fehlt)');" 2>/dev/null
echo "== version =="
curl -sS -m5 http://127.0.0.1:8000/version; echo
echo DIAG_PUBLISHED_DONE
