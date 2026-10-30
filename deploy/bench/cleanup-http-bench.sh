#!/usr/bin/env bash
set -uo pipefail
cd /home/ifs/ifs
rm -f docker-compose.yml.tune-bak
rm -f /tmp/ifs-http-bench-512m.bin /tmp/ifs-ab-256m.bin /tmp/ifs-read-persist.bin \
      /tmp/ifs-s3-profile.bin /tmp/ifs-s3-copy.bin /tmp/ifs-s3-read.bin \
      /tmp/ifs-checksum.bin /tmp/ifs-chunks.bin /tmp/ifs-sse-ab.bin \
      /tmp/bench-http-pipeline.bin /tmp/ifs-http-parts.bin /tmp/weed-write.bin \
      /tmp/weed-write-256.bin /tmp/ifs-profile.bin /tmp/ifs-filer-write.bin \
      /tmp/ifs-presign-write.bin /tmp/ifs-read-persist.bin
docker compose exec -T api python - <<'PY'
import os
from ifs.config import get_settings
from ifs.core import content
settings = get_settings()
client = content.get_s3_client()
keys = [
    "bench/read-persist.bin",
    "bench/profile-write.bin",
    "bench/checksum.bin",
    "bench/chunks.bin",
    "bench/presign-write.bin",
]
for key in keys:
    try:
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
        print("geloescht:", key)
    except Exception as exc:
        print("nicht geloescht:", key, type(exc).__name__)
PY
echo CLEANUP_BENCH_HTTP_DONE
