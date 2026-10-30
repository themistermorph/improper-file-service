#!/usr/bin/env bash
set -euo pipefail
cd /home/ifs/ifs
docker compose build
docker compose up -d api
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:8000/healthz; echo
echo API_RESTART_DONE
