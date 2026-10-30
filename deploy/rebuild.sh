#!/usr/bin/env bash
# Baut das gemeinsame Image neu und startet api/ftp/worker neu (mit Zeitmessung).
set -uo pipefail
cd /home/ifs/ifs
start=$(date +%s)
LOG=/tmp/ifs-rebuild.log
if ! docker compose build >"$LOG" 2>&1; then
  echo BUILD_FAIL; tail -n 30 "$LOG"; exit 1
fi
if ! docker compose up -d api ftp worker >>"$LOG" 2>&1; then
  echo UP_FAIL; tail -n 30 "$LOG"; exit 1
fi
end=$(date +%s)
echo "build_up_seconds=$((end-start))"
grep -E 'CACHED|DONE [0-9]|exporting layers' "$LOG" | tail -6
echo "== ps =="
docker compose ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'
echo REBUILD_DONE
