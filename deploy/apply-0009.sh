#!/usr/bin/env bash
# Wendet Migration 0009 an (öffentlicher Anzeigename), idempotent.
set -euo pipefail
cd /home/ifs/ifs
docker compose exec -T db psql -U ifs -d ifs -v ON_ERROR_STOP=1 < migrations/sql/0009_published_public_name.sql
echo "MIGRATION_0009_OK"
