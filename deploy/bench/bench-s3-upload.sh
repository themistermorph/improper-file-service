#!/usr/bin/env bash
# Benchmark: S3-Upload 64 MiB mit verschiedenen Multipart-/Concurrency-Einstellungen.
set -uo pipefail
cd /home/ifs/ifs
docker compose exec -T api python - <<'PY'
import os
import time

from boto3.s3.transfer import TransferConfig

from ifs.config import get_settings
from ifs.core import content

settings = get_settings()
print("s3_endpoint:", settings.s3_endpoint_url, "use_ssl:", settings.s3_use_ssl)
path = "/tmp/bench-s3.bin"
size = 64 * 1024 * 1024
with open(path, "wb") as handle:
    handle.write(os.urandom(size))
client = content.get_s3_client()
configs = [
    ("default", None),
    ("chunk16/conc8", TransferConfig(multipart_threshold=8 * 1024 * 1024,
                                     multipart_chunksize=16 * 1024 * 1024,
                                     max_concurrency=8)),
    ("chunk32/conc8", TransferConfig(multipart_threshold=8 * 1024 * 1024,
                                     multipart_chunksize=32 * 1024 * 1024,
                                     max_concurrency=8)),
    ("chunk8/conc16", TransferConfig(multipart_threshold=8 * 1024 * 1024,
                                     multipart_chunksize=8 * 1024 * 1024,
                                     max_concurrency=16)),
]
for name, cfg in configs:
    key = f"bench/s3-{name.replace('/', '-')}.bin"
    t0 = time.perf_counter()
    if cfg is None:
        client.upload_file(path, settings.s3_bucket, key)
    else:
        client.upload_file(path, settings.s3_bucket, key, Config=cfg)
    t1 = time.perf_counter()
    print(f"{name:15} {size / (t1 - t0) / 1e6:6.1f} MB/s ({t1 - t0:.2f}s)")
    client.delete_object(Bucket=settings.s3_bucket, Key=key)
os.unlink(path)
PY
echo BENCH_S3_UPLOAD_DONE
