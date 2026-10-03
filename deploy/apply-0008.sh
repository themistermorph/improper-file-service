#!/usr/bin/env bash
# Wendet Migration 0008 an (öffentliche Veröffentlichungen), idempotent.
set -euo pipefail
cd /home/ifs/ifs
docker compose exec -T db psql -U ifs -d ifs -v ON_ERROR_STOP=1 < migrations/sql/0008_published_entries.sql
echo "MIGRATION_0008_OK"
