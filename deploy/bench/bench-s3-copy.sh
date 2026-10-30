#!/usr/bin/env bash
# Misst S3-CopyObject und Multipart-Upload auf dem Testsystem.
set -uo pipefail
cd /home/ifs/ifs
docker compose exec -T api python - <<'PY'
import os
import time

from boto3.s3.transfer import TransferConfig

from ifs.config import get_settings
from ifs.core import content

settings = get_settings()
size = 256 * 1024 * 1024
path = "/tmp/bench-s3-copy.bin"
with open(path, "wb") as handle:
    handle.write(os.urandom(size))
client = content.get_s3_client()
cfg = TransferConfig(multipart_threshold=8 * 1024 * 1024,
                     multipart_chunksize=16 * 1024 * 1024,
                     max_concurrency=8)
src = "bench/copy-src.bin"
dst = "bench/copy-dst.bin"
try:
    t0 = time.perf_counter()
    client.upload_file(path, settings.s3_bucket, src, Config=cfg)
    t1 = time.perf_counter()
    print(f"upload   {size/(t1-t0)/1e6:7.1f} MB/s ({t1-t0:.2f}s)")
    t2 = time.perf_counter()
    client.copy_object(Bucket=settings.s3_bucket, Key=dst,
                       CopySource={"Bucket": settings.s3_bucket, "Key": src})
    t3 = time.perf_counter()
    print(f"copy     {size/(t3-t2)/1e6:7.1f} MB/s ({t3-t2:.2f}s)")
    t4 = time.perf_counter()
    body = client.get_object(Bucket=settings.s3_bucket, Key=dst)["Body"]
    read = 0
    while True:
        chunk = body.read(4 * 1024 * 1024)
        if not chunk:
            break
        read += len(chunk)
    t5 = time.perf_counter()
    print(f"download {read/(t5-t4)/1e6:7.1f} MB/s ({t5-t4:.2f}s) bytes={read}")
finally:
    for key in (src, dst):
        try:
            client.delete_object(Bucket=settings.s3_bucket, Key=key)
        except Exception:
            pass
    os.unlink(path)
PY
echo BENCH_S3_COPY_DONE
