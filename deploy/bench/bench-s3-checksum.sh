#!/usr/bin/env bash
# Prueft, ob Checksummen/Parallelitaet im boto3-Client den Schreibdurchsatz begrenzen.
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
path = "/tmp/ifs-checksum.bin"
size = 256 * 1024 * 1024
mib = 1024 * 1024
with open(path, "wb") as handle:
    remaining = size
    while remaining:
        chunk = os.urandom(min(8 * mib, remaining))
        handle.write(chunk)
        remaining -= len(chunk)

base = dict(
    endpoint_url=settings.s3_endpoint_url,
    aws_access_key_id=settings.s3_access_key,
    aws_secret_access_key=settings.s3_secret_key,
    region_name=settings.s3_region,
    use_ssl=settings.s3_use_ssl,
)
default = boto3.client("s3", **base)
nocheck = boto3.client(
    "s3",
    config=Config(
        signature_version="s3v4",
        s3={"addressing_style": "path"},
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    ),
    **base,
)
key = "bench/checksum.bin"

def put(client):
    t0 = time.perf_counter()
    with open(path, "rb") as handle:
        client.put_object(Bucket=settings.s3_bucket, Key=key, Body=handle, ContentLength=size)
    return time.perf_counter() - t0

def multi(client, chunk, conc):
    cfg = TransferConfig(multipart_threshold=8*mib, multipart_chunksize=chunk*mib,
                         max_concurrency=conc)
    t0 = time.perf_counter()
    client.upload_file(path, settings.s3_bucket, key, Config=cfg)
    return time.perf_counter() - t0

try:
    for label, fn in [
        ("default put", lambda: put(default)),
        ("checksum-off put", lambda: put(nocheck)),
        ("checksum-off multi 16/8", lambda: multi(nocheck, 16, 8)),
        ("checksum-off multi 16/1", lambda: multi(nocheck, 16, 1)),
        ("default multi 16/8", lambda: multi(default, 16, 8)),
    ]:
        dt = fn()
        print(f"{label:26} {size/dt/1e6:7.1f} MB/s ({dt:.2f}s)")
finally:
    for client in (default, nocheck):
        try:
            client.delete_object(Bucket=settings.s3_bucket, Key=key)
        except Exception:
            pass
    os.unlink(path)
PY
echo CHECKSUM_AB_DONE
