#!/usr/bin/env bash
# Wendet Migration 0010 (published_entries.public_path + Unique-Index) an.
set -euo pipefail
cd /home/ifs/ifs

docker compose exec -T db psql -v ON_ERROR_STOP=1 -U ifs -d ifs \
    < migrations/sql/0010_published_public_path.sql

echo "== published_entries =="
docker compose exec -T db psql -U ifs -d ifs -c "\d published_entries" \
    | grep -E "public_path|uq_published" || true
echo APPLY_PUBLISHED_PATH_MIGRATION_DONE
