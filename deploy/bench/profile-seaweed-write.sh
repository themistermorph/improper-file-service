#!/usr/bin/env bash
# Profil: S3-Schreiblast mit CPU/IO-Sampling.
set -uo pipefail
cd /home/ifs/ifs

echo "== Tools =="
command -v iostat || true
command -v pidstat || true

(
  while true; do
    date +%H:%M:%S
    cat /proc/loadavg
    docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}} block={{.BlockIO}}' 2>/dev/null | tr '\n' ';'
    echo
    sleep 1
  done
) >/tmp/ifs-s3-profile.log 2>&1 &
SAMPLER=$!
trap 'kill $SAMPLER 2>/dev/null || true' EXIT

echo "== S3-Multipart 256 MiB =="
docker compose exec -T api python - <<'PY'
import os, time
from boto3.s3.transfer import TransferConfig
from ifs.config import get_settings
from ifs.core import content

settings = get_settings()
client = content.get_s3_client()
path = "/tmp/ifs-profile.bin"
size = 256 * 1024 * 1024
mib = 1024 * 1024
with open(path, "wb") as handle:
    remaining = size
    while remaining:
        chunk = os.urandom(min(8 * mib, remaining))
        handle.write(chunk)
        remaining -= len(chunk)
key = "bench/profile-write.bin"
try:
    for label, cfg in [
        ("multipart 16/8", TransferConfig(multipart_threshold=8*mib,
                                           multipart_chunksize=16*mib,
                                           max_concurrency=8)),
        ("multipart 8/4", TransferConfig(multipart_threshold=8*mib,
                                          multipart_chunksize=8*mib,
                                          max_concurrency=4)),
    ]:
        t0 = time.perf_counter()
        client.upload_file(path, settings.s3_bucket, key, Config=cfg)
        dt = time.perf_counter() - t0
        print(f"{label:18} {size/dt/1e6:7.1f} MB/s ({dt:.2f}s)")
    t0 = time.perf_counter()
    with open(path, "rb") as handle:
        client.put_object(Bucket=settings.s3_bucket, Key=key, Body=handle, ContentLength=size)
    dt = time.perf_counter() - t0
    print(f"{'put_object':18} {size/dt/1e6:7.1f} MB/s ({dt:.2f}s)")
finally:
    try:
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
    except Exception:
        pass
    os.unlink(path)
PY

kill $SAMPLER 2>/dev/null || true
wait $SAMPLER 2>/dev/null || true
trap - EXIT
echo "== Sampling =="
cat /tmp/ifs-s3-profile.log
echo PROFILE_SEAWEED_DONE
