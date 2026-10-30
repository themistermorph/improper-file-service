#!/usr/bin/env bash
# Profiliert den FTP-Upload-Pfad ohne TLS/Client: has_perm, open+write, finalize.
set -uo pipefail
cd /home/ifs/ifs

USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"

docker compose exec -T ftp python - "$USER" "$PASS" <<'PY'
import ftplib
import ssl
import sys
import time
from types import SimpleNamespace

from ifs.ftp.fs import IFSFilesystem
from ifs.ftp.server import IFSAuthorizer

user, password = sys.argv[1], sys.argv[2]
channel = SimpleNamespace(
    username=user,
    remote_ip="127.0.0.1",
    use_gmt_times=False,
    encoding="utf-8",
    unicode_errors="strict",
)
fs = IFSFilesystem.__new__(IFSFilesystem)
fs.cmd_channel = channel
fs._pending = {}
authorizer = IFSAuthorizer()

N = 20


def bench(label, fn):
    start = time.perf_counter()
    for index in range(N):
        fn(index)
    elapsed = time.perf_counter() - start
    print(f"{label:24s} {elapsed / N * 1000:7.2f} ms/op  ({N / elapsed:6.1f} ops/s)")


def has_perm(index):
    authorizer.has_perm(user, "w", f"/prof-{index}.bin")


def open_write(index):
    writer = fs._open_writer(f"/prof-{index}.bin", "wb")
    writer.write(b"x" * 1024)
    writer.close()


def finalize(index):
    fs.finalize_received(f"/prof-{index}.bin")


bench("has_perm", has_perm)
bench("_open_writer + write", open_write)
bench("finalize_received", finalize)

ftp = ftplib.FTP_TLS(context=ssl._create_unverified_context())
ftp.connect("127.0.0.1", 21, timeout=30)
ftp.login(user, password)
ftp.prot_p()
for index in range(N):
    try:
        ftp.delete(f"prof-{index}.bin")
    except Exception:
        pass
ftp.quit()
print("PROFILE_FTP_PATH_DONE")
PY
