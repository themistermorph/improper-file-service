#!/usr/bin/env bash
# Wiederholte A/B-Messung des Download-Datenpfads (warm cache).
set -uo pipefail
cd /home/ifs/ifs
docker compose exec -T api python - <<'PY'
import os
import time

from boto3.s3.transfer import TransferConfig

from ifs.config import get_settings
from ifs.core import content

settings = get_settings()
client = content.get_s3_client()
path = "/tmp/bench-http-ab.bin"
size = 256 * 1024 * 1024
key = "bench/http-ab.bin"
mib = 1024 * 1024
with open(path, "wb") as handle:
    remaining = size
    while remaining:
        chunk = os.urandom(min(8 * mib, remaining))
        handle.write(chunk)
        remaining -= len(chunk)

def old_way(chunk):
    t0 = time.perf_counter()
    body = content.get_object(key)["Body"]
    got = 0
    for block in content.stream_out(body, chunk):
        got += len(block)
    return got, time.perf_counter() - t0

def new_way(parallelism, chunk):
    t0 = time.perf_counter()
    got = 0
    for block in content.iter_object(
        key, start=0, end=size - 1, total_size=size,
        chunk_size=chunk, parallelism=parallelism,
    ):
        got += len(block)
    return got, time.perf_counter() - t0

cases = [
    ("old 1MiB", lambda: old_way(1 * mib)),
    ("old 4MiB", lambda: old_way(4 * mib)),
    ("new p1 4MiB", lambda: new_way(1, 4 * mib)),
    ("new p2 4MiB", lambda: new_way(2, 4 * mib)),
    ("new p4 4MiB", lambda: new_way(4, 4 * mib)),
]
try:
    client.upload_file(
        path, settings.s3_bucket, key,
        Config=TransferConfig(multipart_threshold=8 * mib,
                              multipart_chunksize=16 * mib,
                              max_concurrency=8),
    )
    results = {name: [] for name, _ in cases}
    for round_no in range(3):
        for name, fn in cases:
            got, dt = fn()
            results[name].append(got / dt / 1e6)
            print(f"round={round_no+1} {name:12s} {got/dt/1e6:7.1f} MB/s ({dt:.2f}s) bytes={got}")
    print("--- Median ---")
    for name, values in results.items():
        values.sort()
        print(f"{name:12s} {values[len(values)//2]:7.1f} MB/s  {values}")
finally:
    try:
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
    except Exception:
        pass
    os.unlink(path)
PY
echo BENCH_HTTP_AB_DONE
