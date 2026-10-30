#!/usr/bin/env bash
# Wendet Migration 0005 an (shares.overwrite), idempotent.
set -euo pipefail
cd /home/ifs/ifs
docker compose exec -T db psql -U ifs -d ifs -v ON_ERROR_STOP=1 < migrations/sql/0005_share_overwrite.sql
echo "MIGRATION_0005_OK"
