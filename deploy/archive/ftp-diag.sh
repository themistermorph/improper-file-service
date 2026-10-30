#!/usr/bin/env bash
# Diagnose des FTPS-Datenkanals (als root via sudo).
set -uo pipefail
cd /home/ifs/ifs
PW="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"

echo "== letzte FTP-Logs =="
docker compose logs --tail=150 ftp

echo
echo "== relevante FTP-Umgebungsvariablen =="
docker compose exec -T ftp sh -lc 'env | grep -E "IFS_FTP" | sort'

echo
echo "== PASV/EPSV-Test (Debug) =="
python3 - "$PW" <<'PY'
import ftplib, ssl, sys
pw = sys.argv[1]
ctx = ssl._create_unverified_context()
ftp = ftplib.FTP_TLS(context=ctx)
ftp.set_debuglevel(2)
try:
    ftp.connect("127.0.0.1", 21, timeout=15)
    ftp.login("admin", pw)
    ftp.prot_p()
    print("NLST:", ftp.nlst("/"))
finally:
    try:
        ftp.quit()
    except Exception:
        ftp.close()
PY
