#!/usr/bin/env bash
# Benchmark: Finalisierungskosten (SHA-256 + S3-Upload) für 64 MiB.
set -uo pipefail
cd /home/ifs/ifs
docker compose exec -T api python - <<'PY'
import os
import time

from ifs.config import get_settings
from ifs.core import content

path = "/tmp/bench-final.bin"
size = 64 * 1024 * 1024
with open(path, "wb") as handle:
    handle.write(os.urandom(size))

t0 = time.perf_counter()
sha = content.sha256_file(path)
t1 = time.perf_counter()
info = content.upload_file(path, sha)
t2 = time.perf_counter()

print(f"sha256:    {size / (t1 - t0) / 1e6:6.1f} MB/s ({t1 - t0:.2f}s)")
print(f"s3-upload: {size / (t2 - t1) / 1e6:6.1f} MB/s ({t2 - t1:.2f}s)")
try:
    content.get_s3_client().delete_object(
        Bucket=get_settings().s3_bucket, Key=info.storage_key
    )
    print("s3-objekt wieder geloescht")
except Exception as exc:  # noqa: BLE001
    print("cleanup-fehler:", exc)
os.unlink(path)
PY
echo BENCH_FINALIZE_DONE
