#!/usr/bin/env bash
set -uo pipefail
cd /home/ifs/ifs
echo "--- letzte 30 Zeilen ---"
docker compose logs --tail=30 s3
echo "--- exit code / oom ---"
docker inspect -f 'exit={{.State.ExitCode}} oom={{.State.OOMKilled}} error={{.State.Error}}' ifs-s3-1
