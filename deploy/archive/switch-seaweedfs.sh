#!/usr/bin/env bash
# Umschaltung auf lokales SeaweedFS: Setup -> Start -> Preflight -> Bereinigungshinweis.
set -euo pipefail
cd /home/ifs/ifs

echo "== 1/4 SeaweedFS-Zugangsdaten vorbereiten =="
bash deploy/setup-seaweedfs.sh

echo "== 2/4 Stack bauen/starten =="
docker compose up -d --build --remove-orphans

echo "== 3/4 Auf S3 warten und Preflight =="
for _ in $(seq 1 30); do
  if docker compose exec -T api python -c "import socket; socket.create_connection(('s3',8333),2)" 2>/dev/null; then
    break
  fi
  sleep 2
done
bash deploy/preflight-s3.sh || true

echo "== 4/4 Status =="
docker compose ps
echo
echo "Bereinigung (Datei-Einträge ohne Blob):"
echo "  docker compose exec -T api python -m ifs.cli cleanup-missing-blobs --remove-empty-folders"
