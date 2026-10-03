#!/usr/bin/env bash
# Verifiziert die FTPS-Durchsatz-Optimierung (Read-Ahead, Puffer, Pool).
set -uo pipefail
cd /home/ifs/ifs
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"

echo "== Konfiguration (im ftp-Container) =="
docker compose exec -T ftp python -c \
  "from ifs.config import get_settings; s=get_settings(); print('read_ahead=', s.ftp_s3_readahead_bytes, 'transfer_buffer=', s.ftp_transfer_buffer_bytes, 'pool=', s.s3_max_pool_connections)"

echo "== Datenkanal-Puffer =="
docker compose exec -T ftp python -c \
  "from ifs.ftp.server import build_handler; h=build_handler(); d=h.dtp_handler; print('ac_in=', d.ac_in_buffer_size, 'ac_out=', d.ac_out_buffer_size)"

echo "== FTP-Smoke =="
bash deploy/ftp-check.sh 2>&1 | tail -n 2

echo "== Durchsatz 64 MiB (lokal) =="
python3 - "$USER" "$PASS" <<'PY'
import ftplib, io, ssl, sys, time

user, password = sys.argv[1], sys.argv[2]
size = 64 * 1024 * 1024
data = b"\x00" * size
ctx = ssl._create_unverified_context()
ftp = ftplib.FTP_TLS(context=ctx)
try:
    ftp.connect("127.0.0.1", 21, timeout=120)
    ftp.login(user, password)
    ftp.prot_p()
    name = "ftps-speed.bin"
    t0 = time.perf_counter()
    ftp.storbinary("STOR " + name, io.BytesIO(data))
    t1 = time.perf_counter()
    out = bytearray()
    t2 = time.perf_counter()
    ftp.retrbinary("RETR " + name, out.extend)
    t3 = time.perf_counter()
    print(f"upload   {size/(t1-t0)/1e6:9.1f} MB/s  ({t1-t0:.2f}s)")
    print(f"download {size/(t3-t2)/1e6:9.1f} MB/s  ({t3-t2:.2f}s)  vollstaendig={len(out)==size}")
    ftp.delete(name)
finally:
    try:
        ftp.quit()
    except Exception:
        ftp.close()
PY
echo VERIFY_FTPS_THROUGHPUT_DONE
