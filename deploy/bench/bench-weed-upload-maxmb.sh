#!/usr/bin/env bash
set -uo pipefail
cd /home/ifs/ifs
docker compose exec -T s3 sh -c '
  set -e
  dd if=/dev/zero of=/tmp/weed-write-256.bin bs=1M count=256 status=none
  for mb in 4 256; do
    echo "-- maxMB=$mb --"
    for i in 1 2; do
      /usr/bin/time -f "run=%e s" weed upload -master=localhost:9333 -maxMB=$mb /tmp/weed-write-256.bin 2>&1 | tail -n 2
    done
  done
  rm -f /tmp/weed-write-256.bin
' || true
echo WEED_UPLOAD_MAXMB_DONE
