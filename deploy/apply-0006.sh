#!/usr/bin/env bash
# Wendet Migration 0006 an (genau eine Wurzel je Benutzer), idempotent.
set -euo pipefail
cd /home/ifs/ifs
docker compose exec -T db psql -U ifs -d ifs -v ON_ERROR_STOP=1 < migrations/sql/0006_per_user_roots.sql
echo "MIGRATION_0006_OK"
