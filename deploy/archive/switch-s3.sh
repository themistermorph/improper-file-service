#!/usr/bin/env bash
# Umschaltung auf externes S3: Preflight -> Neustart des Stacks.
# Danach ggf. Bereinigung:  docker compose exec -T api python -m ifs.cli cleanup-missing-blobs
set -euo pipefail
cd /home/ifs/ifs

echo "== 1/3 Preflight =="
bash deploy/preflight-s3.sh

echo "== 2/3 Stack neu bauen/starten =="
docker compose up -d --build --remove-orphans

echo "== 3/3 Status =="
docker compose ps
echo
echo "Optional jetzt bereinigen (Dateien ohne Blob entfernen):"
echo "  docker compose exec -T api python -m ifs.cli cleanup-missing-blobs"
