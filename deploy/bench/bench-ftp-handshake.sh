#!/usr/bin/env bash
# Zerlegt den Per-File-Overhead: Control-RTT, PASV+TLS-Handshake, leeres STOR, 1-KiB-STOR.
set -uo pipefail
cd /home/ifs/ifs

echo "== load =="
uptime

USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"

python3 - "$USER" "$PASS" <<'PY'
import ftplib
import io
import socket
import ssl
import sys
import time

user, password = sys.argv[1], sys.argv[2]
ftp = ftplib.FTP_TLS(context=ssl._create_unverified_context())
ftp.connect("127.0.0.1", 21, timeout=60)
ftp.login(user, password)
ftp.prot_p()
ftp.set_pasv(True)

N = 100


def bench(label, fn):
    start = time.perf_counter()
    for index in range(N):
        fn(index)
    elapsed = time.perf_counter() - start
    print(f"{label:30s} {elapsed / N * 1000:8.2f} ms/op  ({N / elapsed:6.1f} ops/s)")


def noop(index):
    ftp.voidcmd("NOOP")


context = ssl._create_unverified_context()


def handshake(index):
    host, port = ftp.makepasv()
    sock = socket.create_connection((host, port), timeout=10)
    tls = context.wrap_socket(sock, server_hostname=host)
    tls.close()


def stor_empty(index):
    ftp.storbinary(f"STOR bench-hs-empty-{index}.bin", io.BytesIO(b""), blocksize=256 * 1024)


def stor_1k(index):
    ftp.storbinary(
        f"STOR bench-hs-1k-{index}.bin", io.BytesIO(b"x" * 1024), blocksize=256 * 1024
    )


bench("NOOP (Control-RTT)", noop)
bench("PASV + TCP + TLS", handshake)
bench("STOR leer (inkl. Finalisierung)", stor_empty)
bench("STOR 1 KiB", stor_1k)

for index in range(N):
    for name in (f"bench-hs-empty-{index}.bin", f"bench-hs-1k-{index}.bin"):
        try:
            ftp.delete(name)
        except Exception:
            pass
ftp.quit()
PY

echo "== load danach =="
uptime
echo BENCH_HANDSHAKE_DONE
