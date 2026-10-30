#!/usr/bin/env bash
# Diagnose 2: APPE/REST sowie SIZE/LIST auf Padding prüfen.
set -uo pipefail
cd /home/ifs/ifs
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
NAME="pad2-$$.bin"
python3 - "$USER" "$PASS" "$NAME" <<'PY'
import ftplib, io, ssl, sys
user, pw, name = sys.argv[1], sys.argv[2], sys.argv[3]
ftp = ftplib.FTP_TLS(context=ssl._create_unverified_context())
ftp.connect("127.0.0.1", 21, timeout=30)
ftp.login(user, pw)
ftp.prot_p()

def size():
    try:
        return ftp.size(name)
    except Exception as exc:  # noqa: BLE001
        return f"ERR {exc}"

def retr():
    data = bytearray()
    ftp.retrbinary("RETR " + name, data.extend)
    return len(data), bytes(data[:12]), bytes(data[-12:])

ftp.storbinary("STOR " + name, io.BytesIO(b"1234567890"))
print("STOR10       size=", size(), "retr=", retr())
ftp.storbinary("APPE " + name, io.BytesIO(b"abcde"))
print("APPE5        size=", size(), "retr=", retr())
ftp.storbinary("STOR " + name, io.BytesIO(b"XYZ"), rest=0)
print("REST0+STOR3  size=", size(), "retr=", retr())
ftp.storbinary("STOR " + name, io.BytesIO(b"XYZ"), rest=5)
print("REST5+STOR3  size=", size(), "retr=", retr())
ftp.storbinary("STOR " + name, io.BytesIO(b"XYZ"), rest=100)
print("REST100+STOR3 size=", size(), "retr=", retr())
lines = []
ftp.retrlines("LIST " + name, lines.append)
print("LIST        ", lines)
ftp.delete(name)
ftp.quit()
PY
echo DIAG_FTP_PADDING2_DONE
