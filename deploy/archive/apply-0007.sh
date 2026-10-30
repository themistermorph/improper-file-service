#!/usr/bin/env bash
# Wendet Migration 0007 an (Performance-Indizes), idempotent.
set -euo pipefail
cd /home/ifs/ifs
docker compose exec -T db psql -U ifs -d ifs -v ON_ERROR_STOP=1 < migrations/sql/0007_performance_indexes.sql
echo "MIGRATION_0007_OK"
