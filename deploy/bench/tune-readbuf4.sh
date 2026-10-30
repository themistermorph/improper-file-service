#!/usr/bin/env bash
# Setzt testweise -volume.readBufferSizeMB=4 und misst S3-Lesedurchsatz.
set -euo pipefail
cd /home/ifs/ifs
cp -f docker-compose.yml docker-compose.yml.tune-bak
sed -i 's/-volume.readBufferSizeMB=1/-volume.readBufferSizeMB=4/' docker-compose.yml
docker compose up -d s3
for _ in $(seq 1 60); do
  if docker compose exec -T s3 sh -c 'wget -qO- http://localhost:8888/ >/dev/null' 2>/dev/null; then break; fi
  sleep 1
done
docker inspect ifs-s3-1 --format '{{join .Config.Cmd " "}}' | tr ' ' '\n' | grep -E 'readBuffer|readerCache|GOMEM|compaction|maintenance' || true
docker compose exec -T api python - <<'PY'
import time
from boto3.s3.transfer import TransferConfig
from ifs.config import get_settings
from ifs.core import content

settings = get_settings()
client = content.get_s3_client()
key = "bench/read-persist.bin"
mib = 1024 * 1024
for run in (1, 2, 3):
    t0 = time.perf_counter()
    body = client.get_object(Bucket=settings.s3_bucket, Key=key)["Body"]
    got = 0
    while True:
        chunk = body.read(4 * mib)
        if not chunk:
            break
        got += len(chunk)
    dt = time.perf_counter() - t0
    print(f"read run={run}: {got/dt/1e6:6.1f} MB/s ({dt:.2f}s)")
PY
echo READBUF4_DONE
