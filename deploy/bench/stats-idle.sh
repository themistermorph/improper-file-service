#!/usr/bin/env bash
set -uo pipefail
cd /home/ifs/ifs
echo "== idle =="
free -h
docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}' | sort
echo "== memory pressure =="
cat /proc/pressure/memory 2>/dev/null || true
cat /proc/pressure/io 2>/dev/null || true
echo IDLE_STATS_DONE
