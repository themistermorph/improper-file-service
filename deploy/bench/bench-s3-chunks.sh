#!/usr/bin/env bash
# S3-Multipart-Chunkgroesse/Concurrency auf dem Testsystem.
set -uo pipefail
cd /home/ifs/ifs
docker compose exec -T api python - <<'PY'
import os
import time

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.client import Config

from ifs.config import get_settings

settings = get_settings()
path = "/tmp/ifs-chunks.bin"
size = 256 * 1024 * 1024
mib = 1024 * 1024
with open(path, "wb") as handle:
    remaining = size
    while remaining:
        chunk = os.urandom(min(8 * mib, remaining))
        handle.write(chunk)
        remaining -= len(chunk)
client = boto3.client(
    "s3",
    endpoint_url=settings.s3_endpoint_url,
    aws_access_key_id=settings.s3_access_key,
    aws_secret_access_key=settings.s3_secret_key,
    region_name=settings.s3_region,
    use_ssl=settings.s3_use_ssl,
    config=Config(
        signature_version="s3v4",
        s3={"addressing_style": "path"},
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    ),
)
key = "bench/chunks.bin"

def upload(chunk_mb, conc):
    cfg = TransferConfig(multipart_threshold=8*mib,
                         multipart_chunksize=chunk_mb*mib,
                         max_concurrency=conc)
    t0 = time.perf_counter()
    client.upload_file(path, settings.s3_bucket, key, Config=cfg)
    return time.perf_counter() - t0

try:
    for chunk, conc in [(5, 4), (5, 8), (8, 4), (8, 8), (16, 4), (16, 8)]:
        times = []
        for _ in range(2):
            dt = upload(chunk, conc)
            times.append(dt)
        best = min(times)
        print(f"chunk={chunk:2d}MiB conc={conc:2d}: {size/best/1e6:6.1f} MB/s best ({', '.join(f'{t:.2f}s' for t in times)})")
    t0 = time.perf_counter()
    with open(path, "rb") as handle:
        client.put_object(Bucket=settings.s3_bucket, Key=key, Body=handle, ContentLength=size)
    dt = time.perf_counter() - t0
    print(f"single PUT          : {size/dt/1e6:6.1f} MB/s ({dt:.2f}s)")
finally:
    try:
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
    except Exception:
        pass
    os.unlink(path)
PY
echo CHUNK_BENCH_DONE
