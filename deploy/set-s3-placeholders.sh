#!/usr/bin/env bash
# Setzt S3-Platzhalter in .env (übrige Einstellungen bleiben erhalten).
set -euo pipefail
cd /home/ifs/ifs

python3 - <<'PY'
import pathlib
import re

path = pathlib.Path(".env")
lines = path.read_text().splitlines()
values = {
    "IFS_S3_ENDPOINT_URL": "",
    "IFS_S3_REGION": "us-east-1",
    "IFS_S3_BUCKET": "ifs",
    "IFS_S3_ACCESS_KEY": "",
    "IFS_S3_SECRET_KEY": "",
    "IFS_S3_USE_SSL": "true",
    "IFS_S3_SSE": "AES256",
}
seen = set()
out = []
for line in lines:
    match = re.match(r"^([A-Z0-9_]+)=", line)
    if match and match.group(1) in values:
        key = match.group(1)
        out.append(f"{key}={values[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in values.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n")
print("S3-Platzhalter in .env gesetzt.")
PY

grep -E '^IFS_S3_' .env
rm -f docker-compose.minio.yml
echo "docker-compose.minio.yml entfernt (falls vorhanden)."
