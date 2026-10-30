#!/usr/bin/env bash
# Weitere S3-Schreibprofile: put_object vs. multipart, parallele Objekte.
set -uo pipefail
cd /home/ifs/ifs
docker compose exec -T api python - <<'PY'
import os
import time
from concurrent.futures import ThreadPoolExecutor

from boto3.s3.transfer import TransferConfig

from ifs.config import get_settings
from ifs.core import content

settings = get_settings()
client = content.get_s3_client()
path = "/tmp/bench-s3-profile.bin"
size = 64 * 1024 * 1024
with open(path, "wb") as handle:
    handle.write(os.urandom(size))

def cleanup(keys):
    for key in keys:
        try:
            client.delete_object(Bucket=settings.s3_bucket, Key=key)
        except Exception:
            pass

def direct(key):
    t0 = time.perf_counter()
    with open(path, "rb") as handle:
        client.put_object(Bucket=settings.s3_bucket, Key=key, Body=handle, ContentLength=size)
    return time.perf_counter() - t0

def multi(key, chunk_mb, conc):
    cfg = TransferConfig(multipart_threshold=8*1024*1024,
                         multipart_chunksize=chunk_mb*1024*1024,
                         max_concurrency=conc)
    t0 = time.perf_counter()
    client.upload_file(path, settings.s3_bucket, key, Config=cfg)
    return time.perf_counter() - t0

try:
    for label, fn in [
        ("put_object", lambda: direct("bench/p1.bin")),
        ("multi 8/8", lambda: multi("bench/p2.bin", 8, 8)),
        ("multi 8/32", lambda: multi("bench/p3.bin", 8, 32)),
        ("multi 5/16", lambda: multi("bench/p4.bin", 5, 16)),
    ]:
        dt = fn()
        print(f"{label:14} {size/dt/1e6:7.1f} MB/s ({dt:.2f}s)")
        cleanup(["bench/p1.bin", "bench/p2.bin", "bench/p3.bin", "bench/p4.bin"])

    # Vier Objekte gleichzeitig (Aggregat).
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: multi(f"bench/par{i}.bin", 8, 4), range(4)))
    dt = time.perf_counter() - t0
    print(f"4x parallel    {4*size/dt/1e6:7.1f} MB/s aggregate ({dt:.2f}s)")
    cleanup([f"bench/par{i}.bin" for i in range(4)])
finally:
    os.unlink(path)
PY
echo BENCH_S3_PROFILE_DONE
