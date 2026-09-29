#!/usr/bin/env bash
# Prüft FTPS lokal auf dem Server (Login, LIST, MKD, STOR, RETR).
set -uo pipefail
CRED=/home/ifs/ifs/ADMIN_CREDENTIALS.txt
USER="$(grep '^username=' "$CRED" | cut -d= -f2)"
PASS="$(grep '^password=' "$CRED" | cut -d= -f2)"
NAME="ftp-smoke-$(date +%s)"

python3 - "$USER" "$PASS" "$NAME" <<'PY'
import ftplib, io, ssl, sys

user, password, name = sys.argv[1], sys.argv[2], sys.argv[3]
ctx = ssl._create_unverified_context()
ftp = ftplib.FTP_TLS(context=ctx)
try:
    ftp.connect("127.0.0.1", 21, timeout=15)
    ftp.login(user, password)
    ftp.prot_p()
    print("PWD:", ftp.pwd())
    print("root count:", len(ftp.nlst()))

    ftp.mkd(name)
    ftp.cwd(name)
    ftp.storbinary("STOR hello.txt", io.BytesIO(b"ftps hello"))
    out = bytearray()
    ftp.retrbinary("RETR hello.txt", out.extend)
    print("RETR:", bytes(out))
    print("NLST:", ftp.nlst())
    print("FTP_OK")
finally:
    try:
        ftp.quit()
    except Exception:
        ftp.close()
PY
