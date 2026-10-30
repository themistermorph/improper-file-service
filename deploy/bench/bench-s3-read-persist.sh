#!/usr/bin/env bash
# Erzeugt ein persistentes 256-MiB-Testobjekt und misst S3-Reads.
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
path = "/tmp/ifs-read-persist.bin"
size = 256 * 1024 * 1024
mib = 1024 * 1024
key = "bench/read-persist.bin"
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
if not os.path.exists(path):
    with open(path, "wb") as handle:
        remaining = size
        while remaining:
            chunk = os.urandom(min(8 * mib, remaining))
            handle.write(chunk)
            remaining -= len(chunk)
try:
    client.head_object(Bucket=settings.s3_bucket, Key=key)
except Exception:
    t0 = time.perf_counter()
    client.upload_file(path, settings.s3_bucket, key,
                       Config=TransferConfig(multipart_threshold=8*mib,
                                             multipart_chunksize=16*mib,
                                             max_concurrency=4))
    print(f"Objekt angelegt in {time.perf_counter()-t0:.2f}s")
finally:
    os.unlink(path)

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
echo READ_PERSIST_DONE
