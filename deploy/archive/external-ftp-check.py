"""Externer FTPS-Test vom Client aus (prüft Steuer- und Datenkanal/passive Ports).

Zugangsdaten über Umgebungsvariablen:
    IFS_FTP_HOST, IFS_FTP_USER, IFS_FTP_PASS, IFS_FTP_PORT (optional)
"""

from __future__ import annotations

import ftplib
import io
import os
import ssl
import time

host = os.environ["IFS_FTP_HOST"]
user = os.environ["IFS_FTP_USER"]
password = os.environ["IFS_FTP_PASS"]
port = int(os.environ.get("IFS_FTP_PORT", "21"))

ctx = ssl._create_unverified_context()
ftp = ftplib.FTP_TLS(context=ctx)
try:
    ftp.connect(host, port, timeout=20)
    ftp.login(user, password)
    ftp.prot_p()
    print("PWD:", ftp.pwd())

    name = f"ext-ftp-{int(time.time())}"
    ftp.mkd(name)
    ftp.cwd(name)
    ftp.storbinary("STOR hello.txt", io.BytesIO(b"externer ftps test"))

    received = bytearray()
    ftp.retrbinary("RETR hello.txt", received.extend)
    print("RETR:", bytes(received))
    print("NLST:", ftp.nlst())
    print("EXTERNAL_FTP_OK")
finally:
    try:
        ftp.quit()
    except Exception:
        ftp.close()
