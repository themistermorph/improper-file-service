#!/usr/bin/env bash
# Benchmark: sequenzielle FTPS-Uploads kleiner Dateien.
set -uo pipefail
cd /home/ifs/ifs

USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"

python3 - "$USER" "$PASS" <<'PY'
import ftplib
import io
import ssl
import sys
import time

user, password = sys.argv[1], sys.argv[2]
ftp = ftplib.FTP_TLS(context=ssl._create_unverified_context())
ftp.connect("127.0.0.1", 21, timeout=60)
ftp.login(user, password)
ftp.prot_p()
ftp.set_pasv(True)

base = bytes(range(256)) * 4  # 1 KiB

def run(size, count):
    payload = (base * ((size // len(base)) + 1))[:size]
    start = time.perf_counter()
    for i in range(count):
        data = i.to_bytes(4, "big") + payload
        ftp.storbinary(
            f"STOR bench-small-{size}-{i}.bin", io.BytesIO(data), blocksize=256 * 1024
        )
    elapsed = time.perf_counter() - start
    print(
        f"{size:>7} B x {count:>3}: {elapsed:6.2f}s  "
        f"{count / elapsed:7.1f} Dateien/s  {elapsed / count * 1000:6.2f} ms/Datei"
    )
    for i in range(count):
        try:
            ftp.delete(f"bench-small-{size}-{i}.bin")
        except Exception:
            pass

for size, count in ((1024, 200), (10 * 1024, 200), (100 * 1024, 100)):
    run(size, count)

ftp.quit()
PY

echo BENCH_SMALL_DONE
