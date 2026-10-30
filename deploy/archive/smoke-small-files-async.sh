#!/usr/bin/env bash
# Prüft die asynchrone Finalisierung: Upload, sofort SIZE/RETR/NLST (Drain), DELE.
set -uo pipefail
cd /home/ifs/ifs

USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"

python3 - "$USER" "$PASS" <<'PY'
import ftplib
import io
import ssl
import sys

user, password = sys.argv[1], sys.argv[2]
ftp = ftplib.FTP_TLS(context=ssl._create_unverified_context())
ftp.connect("127.0.0.1", 21, timeout=30)
ftp.login(user, password)
ftp.prot_p()
ftp.set_pasv(True)

name = "async-smoke.txt"
data = b"asynchron-finalisiert"
ftp.storbinary(f"STOR {name}", io.BytesIO(data), blocksize=256 * 1024)
# SIZE drain't die Finalisierung; danach muss der Inhalt sichtbar/lesbar sein.
size = ftp.size(name)
assert size == len(data), f"SIZE {size} != {len(data)}"
buffer = io.BytesIO()
ftp.retrbinary(f"RETR {name}", buffer.write)
assert buffer.getvalue() == data, "RETR-Inhalt weicht ab"
assert name in ftp.nlst(), "NLST enthält die Datei nicht"
ftp.delete(name)
print(f"ASYNC_SMOKE_OK size={size}")
ftp.quit()
PY
