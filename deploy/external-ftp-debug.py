"""Zeigt die PASV-Antwort eines externen FTPS-Clients (Debug)."""

from __future__ import annotations

import ftplib
import os
import ssl

host = os.environ["IFS_FTP_HOST"]
user = os.environ["IFS_FTP_USER"]
password = os.environ["IFS_FTP_PASS"]

ctx = ssl._create_unverified_context()
ftp = ftplib.FTP_TLS(context=ctx)
ftp.set_debuglevel(2)
try:
    ftp.connect(host, 21, timeout=20)
    ftp.login(user, password)
    ftp.prot_p()
    print("NLST:", ftp.nlst("/"))
finally:
    try:
        ftp.quit()
    except Exception:
        ftp.close()
