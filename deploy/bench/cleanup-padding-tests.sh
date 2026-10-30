#!/usr/bin/env bash
# Entfernt die Padding-Diagnose-Artefakte (padtest-*, pad2-*) aus dem Admin-Root.
set -uo pipefail
cd /home/ifs/ifs
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
names = ftp.nlst()
removed = []
for name in names:
    if fnmatch.fnmatch(name, "padtest-*") or fnmatch.fnmatch(name, "pad2-*"):
        try:
            ftp.delete(name)
            removed.append(name)
        except Exception as exc:  # noqa: BLE001
            print("Fehler beim Löschen:", name, exc)
print("entfernt:", removed)
ftp.quit()
PY
echo CLEANUP_PADDING_DONE
