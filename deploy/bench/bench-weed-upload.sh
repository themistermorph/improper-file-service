#!/usr/bin/env bash
set -uo pipefail
cd /home/ifs/ifs
echo "== weed upload help =="
docker compose exec -T s3 sh -c 'weed upload -h 2>&1 | head -n 40' || true
echo "== Volume-Direktschreiben 256 MiB =="
docker compose exec -T s3 sh -c '
  set -e
  dd if=/dev/zero of=/tmp/weed-write.bin bs=1M count=256 status=none
  for i in 1 2 3; do
    /usr/bin/time -f "run=%e s" weed upload -master=localhost:9333 /tmp/weed-write.bin 2>&1 | tail -n 3
  done
  rm -f /tmp/weed-write.bin
' || true
echo WEED_UPLOAD_DONE
