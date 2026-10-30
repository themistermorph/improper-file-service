#!/usr/bin/env bash
set -uo pipefail
cd /home/ifs/ifs
echo "== IFS S3-Konfiguration =="
docker compose exec -T api python -c \
  "from ifs.config import get_settings; s=get_settings(); print('sse=', repr(s.s3_sse), 'bucket=', s.s3_bucket, 'endpoint=', s.s3_endpoint_url)"
echo "== SeaweedFS Flags im Prozess =="
docker compose exec -T s3 sh -c 'ps -o pid,pcpu,pmem,rss,args -p 1; echo; cat /proc/1/cmdline | tr "\0" " "; echo' 2>/dev/null || true
echo "== SSE A/B (256 MiB put_object) =="
docker compose exec -T api python - <<'PY'
import os, time
from ifs.config import get_settings
from ifs.core import content

settings = get_settings()
client = content.get_s3_client()
path = "/tmp/ifs-sse-ab.bin"
size = 256 * 1024 * 1024
mib = 1024 * 1024
with open(path, "wb") as handle:
    remaining = size
    while remaining:
        chunk = os.urandom(min(8 * mib, remaining))
        handle.write(chunk)
        remaining -= len(chunk)
try:
    for label, extra in [
        ("ohne SSE", {}),
        ("mit AES256", {"ServerSideEncryption": "AES256"}),
        ("ohne SSE (2)", {}),
    ]:
        key = "bench/sse-" + label.replace(" ", "-") + ".bin"
        t0 = time.perf_counter()
        with open(path, "rb") as handle:
            client.put_object(Bucket=settings.s3_bucket, Key=key, Body=handle,
                              ContentLength=size, **extra)
        dt = time.perf_counter() - t0
        print(f"{label:14} {size/dt/1e6:7.1f} MB/s ({dt:.2f}s)")
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
finally:
    os.unlink(path)
PY
echo SSE_AB_DONE
