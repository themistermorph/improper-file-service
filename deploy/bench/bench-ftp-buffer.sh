#!/usr/bin/env bash
# Benchmark: Einfluss des Server-Transferpuffers (mit Warten auf FTP-Bereitschaft).
set -uo pipefail
cd /home/ifs/ifs
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
NAME="bench-buffer.bin"
SIZE_MB=64

wait_ftp() {
  for _ in $(seq 1 60); do
    if python3 - <<'PY' >/dev/null 2>&1
import socket
s = socket.create_connection(("127.0.0.1", 21), 2)
banner = s.recv(1024)
s.close()
raise SystemExit(0 if banner.startswith(b"220") else 1)
PY
    then
      return 0
    fi
    sleep 1
  done
  echo "FTP nicht bereit"
  return 1
}

run_upload() {
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
t0 = time.perf_counter()
ftp.storbinary("STOR " + name, io.BytesIO(data), blocksize=262144)
t1 = time.perf_counter()
final = None
for _ in range(1200):
    try:
        if ftp.size(name) == size:
            final = time.perf_counter()
            break
    except Exception:
        pass
    time.sleep(0.05)
try:
    ftp.delete(name)
except Exception:
    pass
ftp.quit()
print(f"transfer {size/(t1-t0)/1e6:.1f} MB/s ({t1-t0:.2f}s), finalisierung {(final-t1) if final else -1:.2f}s")
PY
}

ORIG_LINE="$(grep '^IFS_FTP_TRANSFER_BUFFER_BYTES=' .env || true)"
restore() {
  if [ -n "$ORIG_LINE" ]; then
    sed -i "s|^IFS_FTP_TRANSFER_BUFFER_BYTES=.*|$ORIG_LINE|" .env
  else
    sed -i '/^IFS_FTP_TRANSFER_BUFFER_BYTES=/d' .env
  fi
  docker compose up -d ftp >/dev/null 2>&1 || true
  wait_ftp >/dev/null 2>&1 || true
}
trap restore EXIT

for VAL in 262144 524288 1048576 2097152; do
  if grep -q '^IFS_FTP_TRANSFER_BUFFER_BYTES=' .env; then
    sed -i "s|^IFS_FTP_TRANSFER_BUFFER_BYTES=.*|IFS_FTP_TRANSFER_BUFFER_BYTES=$VAL|" .env
  else
    echo "IFS_FTP_TRANSFER_BUFFER_BYTES=$VAL" >> .env
  fi
  docker compose up -d ftp >/dev/null 2>&1
  wait_ftp
  echo -n "buffer=$VAL -> "
  run_upload
done
echo BENCH_BUFFER_DONE
