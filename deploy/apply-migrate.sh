#!/usr/bin/env bash
# Baut das gemeinsame Image neu und startet api/ftp/worker neu (mit Zeitmessung).
set -uo pipefail
cd /home/ifs/ifs
start=$(date +%s)
if ! docker compose build >/tmp/ifs-migrate-build.log 2>&1; then
  echo BUILD_FAIL; tail -n 30 /tmp/ifs-migrate-build.log; exit 1
fi
if ! docker compose up -d api ftp worker >>/tmp/ifs-migrate-build.log 2>&1; then
  echo UP_FAIL; tail -n 30 /tmp/ifs-migrate-build.log; exit 1
fi
end=$(date +%s)
echo "build_up_seconds=$((end-start))"
grep -E 'CACHED|DONE [0-9]|exporting layers' /tmp/ifs-migrate-build.log | tail -6
echo "== ps =="
docker compose ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'
echo APPLY_MIGRATE_DONE
