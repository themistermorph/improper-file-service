#!/usr/bin/env bash
# Benchmark: FTPS-Upload-Durchsatz (lokal) + Finalisierungslatenz.
set -uo pipefail
cd /home/ifs/ifs
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
NAME="bench-upload.bin"
SIZE_MB="${1:-64}"

echo "== Settings =="
grep -E '^IFS_(FTP_TRANSFER_BUFFER_BYTES|FTP_S3_READAHEAD_BYTES|S3_MAX_POOL_CONNECTIONS|MAX_UPLOAD_SIZE)=' .env || true
docker compose exec -T ftp python -c "from ifs.config import get_settings; s=get_settings(); print('transfer_buffer=',s.ftp_transfer_buffer_bytes,'readahead=',s.ftp_s3_readahead_bytes,'pool=',s.s3_max_pool_connections,'max_upload=',s.max_upload_size)" 2>/dev/null

echo "== Upload ${SIZE_MB} MiB (FTPS, lokal) =="
python3 - "$USER" "$PASS" "$NAME" "$SIZE_MB" <<'PY'
import ftplib, io, os, ssl, sys, time

user, pw, name, mb = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
size = mb * 1024 * 1024
data = os.urandom(size)
ftp = ftplib.FTP_TLS(context=ssl._create_unverified_context())
ftp.connect("127.0.0.1", 21, timeout=300)
ftp.login(user, pw)
ftp.prot_p()
try:
    ftp.delete(name)
except Exception:
    pass
for blocksize in (8192, 65536, 262144):
    t0 = time.perf_counter()
    ftp.storbinary("STOR " + name, io.BytesIO(data), blocksize=blocksize)
    t1 = time.perf_counter()
    final = None
    for _ in range(600):
        try:
            if ftp.size(name) == size:
                final = time.perf_counter()
                break
        except Exception:
            pass
        time.sleep(0.05)
    fin = (final - t1) if final else float("nan")
    print(
        f"  blocksize={blocksize // 1024:4d} KiB: "
        f"{size / (t1 - t0) / 1e6:8.1f} MB/s (Transfer {t1 - t0:.2f}s), "
        f"Finalisierung {fin:.2f}s"
    )
try:
    ftp.delete(name)
except Exception:
    pass
ftp.quit()
PY
echo BENCH_FTP_UPLOAD_DONE
