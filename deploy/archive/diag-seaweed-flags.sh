#!/usr/bin/env bash
set -uo pipefail
cd /home/ifs/ifs
echo "== weed server flags (write/sync/buffer/volume) =="
docker compose exec -T s3 sh -c 'weed server -h 2>&1' | grep -iE 'write|sync|buffer|volume|disk|index|compact|memory|cache|fsync|concurr|max' | head -n 120
echo
echo "== weed volume flags (write/sync/buffer) =="
docker compose exec -T s3 sh -c 'weed volume -h 2>&1' | grep -iE 'write|sync|buffer|volume|disk|index|compact|memory|cache|fsync|concurr|max' | head -n 120
