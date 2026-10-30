#!/usr/bin/env bash
# Vergleicht Root-Listing über API und per FTPS.
set -uo pipefail
cd /home/ifs/ifs
PW="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"

T="$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$PW\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')"

echo "--- API: Kinder der Wurzel ---"
curl -s -H "Authorization: Bearer $T" http://127.0.0.1:8000/api/entries \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(len(d), [e["name"] for e in d])'

echo "--- API: Wurzel ---"
curl -s -H "Authorization: Bearer $T" http://127.0.0.1:8000/api/root

echo
echo "--- FTP: NLST ---"
python3 - "$PW" <<'PY'
import ftplib, ssl, sys
ctx = ssl._create_unverified_context()
ftp = ftplib.FTP_TLS(context=ctx)
ftp.connect("127.0.0.1", 21, timeout=15)
ftp.login("admin", sys.argv[1])
ftp.prot_p()
print("pwd:", ftp.pwd())
print("nlst /:", ftp.nlst("/"))
print("nlst .:", ftp.nlst())
try:
    print("MLSD:", [n for n, _ in ftp.mlsd()][:20])
except Exception as exc:
    print("MLSD-Fehler:", exc)
ftp.quit()
PY
