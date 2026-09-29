#!/usr/bin/env bash
# Baut und startet den IFS-Compose-Stack (läuft als root via sudo).
set -euo pipefail
cd /home/ifs/ifs

echo "== Build =="
docker compose build

echo "== Start =="
docker compose up -d

echo "== Status =="
docker compose ps
