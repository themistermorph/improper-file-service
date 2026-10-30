#!/usr/bin/env bash
# Diagnose: Wird ein per FTPS hochgeladener 10-Byte-Inhalt gepadded?
set -uo pipefail
cd /home/ifs/ifs
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
NAME="padtest-$$.bin"

echo "== 1) FTPS-Upload: $NAME, 10 Bytes =="
python3 - "$USER" "$PASS" "$NAME" <<'PY'
import ftplib, io, ssl, sys
user, pw, name = sys.argv[1], sys.argv[2], sys.argv[3]
ftp = ftplib.FTP_TLS(context=ssl._create_unverified_context())
ftp.connect("127.0.0.1", 21, timeout=30)
ftp.login(user, pw)
ftp.prot_p()
ftp.storbinary("STOR " + name, io.BytesIO(b"1234567890"))
print("NLST:", ftp.nlst())
ftp.quit()
PY

echo "== 2) DB-Größen =="
docker compose exec -T db psql -U ifs -d ifs -A -t -F' | ' -c "
select e.name, e.size, b.size, b.storage_key
from entries e
join versions v on v.id = e.current_version_id
join blobs b on b.id = v.blob_id
where e.name = '$NAME' order by e.created_at desc limit 1;" 2>/dev/null

echo "== 3) S3-Objekt (head/get) =="
docker compose exec -T api python - "$NAME" <<'PY'
import sys
from sqlalchemy import select
from ifs.config import get_settings
from ifs.core import content
from ifs.db import session_scope
from ifs.models import Blob, Entry, Version
name = sys.argv[1]
with session_scope() as db:
    entry = db.execute(select(Entry).where(Entry.name == name)).scalars().first()
    version = db.get(Version, entry.current_version_id)
    blob = db.get(Blob, version.blob_id)
    key, size = blob.storage_key, blob.size
client = content.get_s3_client()
head = client.head_object(Bucket=get_settings().s3_bucket, Key=key)
data = client.get_object(Bucket=get_settings().s3_bucket, Key=key)["Body"].read()
print("key=", key, "db.size=", size, "head=", head["ContentLength"], "get_len=", len(data), "prefix=", data[:16])
PY

echo "== 4) FTP-Download =="
python3 - "$USER" "$PASS" "$NAME" <<'PY'
import ftplib, ssl, sys
user, pw, name = sys.argv[1], sys.argv[2], sys.argv[3]
ftp = ftplib.FTP_TLS(context=ssl._create_unverified_context())
ftp.connect("127.0.0.1", 21, timeout=30)
ftp.login(user, pw)
ftp.prot_p()
data = bytearray()
ftp.retrbinary("RETR " + name, data.extend)
print("len=", len(data), "prefix=", bytes(data[:16]), "suffix=", bytes(data[-16:]))
ftp.delete(name)
ftp.quit()
PY

echo DIAG_FTP_PADDING_DONE
