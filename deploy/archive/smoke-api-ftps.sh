#!/usr/bin/env bash
# Smoke-Test nach dem Rollout: API antwortet, FTPS-Login funktioniert.
set -uo pipefail
cd /home/ifs/ifs

echo "=== API ==="
docker compose exec -T api python - <<'PY'
import urllib.error
import urllib.request

for path in ("/", "/api/auth/me"):
    try:
        response = urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=5)
        print(path, response.status, response.headers.get("content-type"))
    except urllib.error.HTTPError as exc:
        print(path, exc.code)
PY

echo "=== FTPS ==="
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
python3 - "$USER" "$PASS" <<'PY'
import ftplib
import ssl
import sys

user, password = sys.argv[1], sys.argv[2]
ftp = ftplib.FTP_TLS(context=ssl._create_unverified_context())
ftp.connect("127.0.0.1", 21, timeout=15)
ftp.login(user, password)
ftp.prot_p()
print("FTPS login ok, root entries:", len(ftp.nlst()))
ftp.quit()
PY

echo SMOKE_DONE
