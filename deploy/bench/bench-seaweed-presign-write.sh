#!/usr/bin/env bash
# Isoliert den SeaweedFS-Schreibpfad ohne boto3-Checksummen/CPU im Client.
set -uo pipefail
cd /home/ifs/ifs
docker compose exec -T api python - <<'PY'
import http.client
import os
import time
import urllib.parse

from ifs.config import get_settings
from ifs.core import content

settings = get_settings()
client = content.get_s3_client()
path = "/tmp/ifs-presign-write.bin"
size = 256 * 1024 * 1024
mib = 1024 * 1024
with open(path, "wb") as handle:
    remaining = size
    while remaining:
        chunk = os.urandom(min(8 * mib, remaining))
        handle.write(chunk)
        remaining -= len(chunk)

def presigned_put(key):
    return client.generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=300,
    )

def put_url(url):
    parsed = urllib.parse.urlparse(url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=600)
    target = parsed.path + ("?" + parsed.query if parsed.query else "")
    conn.putrequest("PUT", target)
    conn.putheader("Host", parsed.netloc)
    conn.putheader("Content-Length", str(size))
    conn.endheaders()
    sent = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(4 * mib)
            if not chunk:
                break
            conn.send(chunk)
            sent += len(chunk)
    response = conn.getresponse()
    response.read()
    conn.close()
    if response.status not in (200, 201):
        raise RuntimeError(f"PUT {response.status}")

try:
    for label, fn in [
        ("presigned S3 PUT", lambda: put_url(presigned_put("bench/presign-write.bin"))),
    ]:
        for run in (1, 2, 3):
            t0 = time.perf_counter()
            fn()
            dt = time.perf_counter() - t0
            print(f"{label} run={run}: {size/dt/1e6:7.1f} MB/s ({dt:.2f}s)")
    client.delete_object(Bucket=settings.s3_bucket, Key="bench/presign-write.bin")

    # Filer-HTTP-Pfad (ohne S3-Gateway) als Gegenprobe.
    for run in (1, 2):
        conn = http.client.HTTPConnection("s3", 8888, timeout=600)
        conn.putrequest("PUT", "/bench/filer-write.bin")
        conn.putheader("Host", "s3")
        conn.putheader("Content-Length", str(size))
        conn.endheaders()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(4 * mib)
                if not chunk:
                    break
                conn.send(chunk)
        response = conn.getresponse()
        response.read()
        conn.close()
        if response.status not in (200, 201, 204):
            print("filer status", response.status)
        dt = time.perf_counter() - t0 if run == 2 else None
        if run == 2:
            print(f"filer PUT run={run}: status={response.status}")
finally:
    try:
        client.delete_object(Bucket=settings.s3_bucket, Key="bench/presign-write.bin")
    except Exception:
        pass
    os.unlink(path)
PY
echo PRESIGN_WRITE_DONE
