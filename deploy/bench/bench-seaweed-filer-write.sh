#!/usr/bin/env bash
# Gegenprobe: Schreiben ueber den Filer-HTTP-Pfad (ohne S3-Gateway).
set -uo pipefail
cd /home/ifs/ifs
docker compose exec -T api python - <<'PY'
import http.client
import os
import time

path = "/tmp/ifs-filer-write.bin"
size = 256 * 1024 * 1024
mib = 1024 * 1024
with open(path, "wb") as handle:
    remaining = size
    while remaining:
        chunk = os.urandom(min(8 * mib, remaining))
        handle.write(chunk)
        remaining -= len(chunk)

def put(connection, target):
    connection.putrequest("PUT", target)
    connection.putheader("Host", "s3")
    connection.putheader("Content-Length", str(size))
    connection.endheaders()
    sent = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(4 * mib)
            if not chunk:
                break
            connection.send(chunk)
            sent += len(chunk)
    response = connection.getresponse()
    body = response.read(200)
    return response.status, body, sent

try:
    for run in (1, 2, 3):
        target = f"/bench/filer-write-{run}.bin"
        conn = http.client.HTTPConnection("s3", 8888, timeout=600)
        t0 = time.perf_counter()
        status, body, sent = put(conn, target)
        dt = time.perf_counter() - t0
        conn.close()
        print(f"filer PUT run={run}: status={status} {sent/dt/1e6:.1f} MB/s ({dt:.2f}s) {body[:120]!r}")
        conn = http.client.HTTPConnection("s3", 8888, timeout=60)
        conn.request("DELETE", target)
        conn.getresponse().read()
        conn.close()
finally:
    os.unlink(path)
PY
echo FILER_WRITE_DONE
