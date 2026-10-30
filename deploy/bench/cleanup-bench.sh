#!/usr/bin/env bash
# Prüft die aktive S3-Upload-Konfiguration und entfernt bench-* Artefakte.
set -uo pipefail
cd /home/ifs/ifs
echo "== Settings =="
docker compose exec -T api python -c "from ifs.config import get_settings; s=get_settings(); print('threshold_mb=',s.s3_multipart_threshold_mb,'chunksize_mb=',s.s3_multipart_chunksize_mb,'concurrency=',s.s3_transfer_concurrency)" 2>/dev/null
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
python3 - "$USER" "$PASS" <<'PY'
import fnmatch
import ftplib
import ssl
import sys

user, pw = sys.argv[1], sys.argv[2]
ftp = ftplib.FTP_TLS(context=ssl._create_unverified_context())
ftp.connect("127.0.0.1", 21, timeout=30)
ftp.login(user, pw)
ftp.prot_p()
names = [n for n in ftp.nlst() if fnmatch.fnmatch(n, "bench-*")]
removed = []
for name in names:
    try:
        ftp.delete(name)
        removed.append(name)
    except Exception as exc:  # noqa: BLE001
        print("Fehler:", name, exc)
print("bench-Artefakte entfernt:", removed)
ftp.quit()
PY
echo CLEANUP_BENCH_DONE
