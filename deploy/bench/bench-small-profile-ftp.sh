#!/usr/bin/env bash
# Wie bench-small-profile.sh, aber im FTP-Container (gleiche Laufzeitumgebung
# wie der echte Upload-Pfad).
set -uo pipefail
cd /home/ifs/ifs

docker compose exec -T ftp python - <<'PY'
import os
import time
import uuid

from sqlalchemy import delete, text

from ifs.core import content
from ifs.core.content import store_blob
from ifs.db import session_scope
from ifs.models import Blob

N = 50
settings = content.get_settings()
data = os.urandom(1024)
paths = []
for i in range(N):
    path = f"/tmp/ifs-small-{i}.bin"
    with open(path, "wb") as handle:
        handle.write(data + i.to_bytes(4, "big"))
    paths.append(path)

shas = [content.sha256_file(path) for path in paths]
keys = []


def bench(label, fn):
    start = time.perf_counter()
    for index, path in enumerate(paths):
        fn(index, path)
    elapsed = time.perf_counter() - start
    print(f"{label:24s} {elapsed / N * 1000:7.2f} ms/op  ({N / elapsed:6.1f} ops/s)")


def session_op(index, path):
    with session_scope() as db:
        db.execute(text("SELECT 1"))


def upload_op(index, path):
    content.upload_file(path, shas[index])


def put_op(index, path):
    key = f"bench/small/{uuid.uuid4().hex}"
    with open(path, "rb") as handle:
        content.get_s3_client().put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=handle,
            ContentLength=os.path.getsize(path),
        )
    keys.append(key)


def store_op(index, path):
    with session_scope() as db:
        store_blob(db, path, sha256=shas[index])


bench("session+select", session_op)
bench("upload_file (PUT)", upload_op)
bench("put_object direct", put_op)
bench("store_blob", store_op)

cas_keys = [f"cas/{sha[:2]}/{sha}" for sha in shas]
all_keys = cas_keys + keys
client = content.get_s3_client()
for key in all_keys:
    try:
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
    except Exception:
        pass
with session_scope() as db:
    db.execute(delete(Blob).where(Blob.storage_key.in_(all_keys)))
for path in paths:
    try:
        os.remove(path)
    except OSError:
        pass
print("PROFILE_SMALL_FTP_DONE")
PY
