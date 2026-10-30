#!/usr/bin/env bash
# Misst den Server-Datenpfad iter_object mit verschiedenen Prefetch-Parametern.
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
path = "/tmp/bench-http-pipeline.bin"
size = 256 * 1024 * 1024
key = "bench/http-pipeline.bin"
mib = 1024 * 1024
with open(path, "wb") as handle:
    remaining = size
    while remaining:
        chunk = os.urandom(min(8 * mib, remaining))
        handle.write(chunk)
        remaining -= len(chunk)
try:
    client.upload_file(
        path, settings.s3_bucket, key,
        Config=TransferConfig(multipart_threshold=8 * mib,
                              multipart_chunksize=16 * mib,
                              max_concurrency=8),
    )
    for parallelism, chunk in [
        (1, 1 * mib), (1, 4 * mib), (2, 1 * mib), (2, 4 * mib),
        (4, 1 * mib), (4, 4 * mib), (4, 8 * mib), (8, 4 * mib),
    ]:
        t0 = time.perf_counter()
        got = 0
        for block in content.iter_object(
            key, start=0, end=size - 1, total_size=size,
            chunk_size=chunk, parallelism=parallelism,
        ):
            got += len(block)
        dt = time.perf_counter() - t0
        print(f"parallel={parallelism} chunk={chunk//mib}MiB  "
              f"{got/dt/1e6:7.1f} MB/s ({dt:.2f}s) bytes={got}")
finally:
    try:
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
    except Exception:
        pass
    os.unlink(path)
PY
echo BENCH_HTTP_PIPELINE_DONE
