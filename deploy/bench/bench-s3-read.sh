#!/usr/bin/env bash
# Misst S3-Lesedurchsatz: ein Stream vs. parallele Range-Streams.
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
path = "/tmp/bench-s3-read.bin"
size = 256 * 1024 * 1024
with open(path, "wb") as handle:
    handle.write(os.urandom(size))
key = "bench/read-src.bin"
try:
    client.upload_file(path, settings.s3_bucket, key,
                       Config=TransferConfig(multipart_threshold=8*1024*1024,
                                             multipart_chunksize=16*1024*1024,
                                             max_concurrency=8))
    def read_range(start, end):
        body = client.get_object(Bucket=settings.s3_bucket, Key=key,
                                 Range=f"bytes={start}-{end}")["Body"]
        total = 0
        while True:
            chunk = body.read(4 * 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
        return total

    t0 = time.perf_counter()
    got = read_range(0, size - 1)
    dt = time.perf_counter() - t0
    print(f"single      {got/dt/1e6:7.1f} MB/s ({dt:.2f}s) bytes={got}")

    for n in (2, 4, 8):
        window = size // n
        ranges = [(i * window, size - 1 if i == n - 1 else (i + 1) * window - 1)
                  for i in range(n)]
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n) as pool:
            got = sum(pool.map(lambda r: read_range(*r), ranges))
        dt = time.perf_counter() - t0
        print(f"parallel {n}  {got/dt/1e6:7.1f} MB/s ({dt:.2f}s) bytes={got}")
finally:
    try:
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
    except Exception:
        pass
    os.unlink(path)
PY
echo BENCH_S3_READ_DONE
