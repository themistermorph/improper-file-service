#!/usr/bin/env bash
# Preflight: prüft die S3-Konfiguration und die Kompatibilität (kein Eingriff am Betrieb).
# Voraussetzung: .env mit IFS_S3_* ist gesetzt und der Stack läuft mindestens mit `db`.
set -uo pipefail
cd /home/ifs/ifs

echo "== S3-Preflight =="
docker compose exec -T api python - <<'PY'
import sys

from ifs.config import get_settings

s = get_settings()
print("endpoint:", s.s3_endpoint_url or "(AWS)")
print("bucket:  ", s.s3_bucket)
print("region:  ", s.s3_region, "| ssl:", s.s3_use_ssl, "| sse:", s.s3_sse or "(aus)")

if not s.s3_access_key or not s.s3_secret_key:
    print("FEHLT: IFS_S3_ACCESS_KEY / IFS_S3_SECRET_KEY in .env setzen.")
    sys.exit(2)
if s.s3_access_key in {"minioadmin", "test"} or s.s3_secret_key in {"minioadmin", "test"}:
    print("WARNUNG: Es sind noch Default-Zugangsdaten gesetzt.")

import boto3
from botocore.config import Config

client = boto3.client(
    "s3",
    endpoint_url=s.s3_endpoint_url,
    aws_access_key_id=s.s3_access_key,
    aws_secret_access_key=s.s3_secret_key,
    region_name=s.s3_region,
    use_ssl=s.s3_use_ssl,
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)
extra = {"ServerSideEncryption": s.s3_sse} if s.s3_sse else {}

try:
    client.head_bucket(Bucket=s.s3_bucket)
    print("HeadBucket: OK")
except Exception as exc:  # noqa: BLE001
    print("HeadBucket:", exc)
    try:
        if s.s3_region and s.s3_region != "us-east-1":
            client.create_bucket(
                Bucket=s.s3_bucket,
                CreateBucketConfiguration={"LocationConstraint": s.s3_region},
            )
        else:
            client.create_bucket(Bucket=s.s3_bucket)
        print("CreateBucket: OK")
    except Exception as exc2:  # noqa: BLE001
        print("CreateBucket FEHLGESCHLAGEN:", exc2)
        sys.exit(1)

key = "ifs-preflight/probe.bin"
data = b"a" * 64
client.put_object(Bucket=s.s3_bucket, Key=key, Body=data, **extra)
print("PutObject: OK")
assert client.get_object(Bucket=s.s3_bucket, Key=key)["Body"].read() == data
print("GetObject: OK")
assert client.get_object(Bucket=s.s3_bucket, Key=key, Range="bytes=0-3")["Body"].read() == data[:4]
print("Range: OK")

mp_key = "ifs-preflight/probe-mp.bin"
mp = client.create_multipart_upload(Bucket=s.s3_bucket, Key=mp_key, **extra)
part = client.upload_part(
    Bucket=s.s3_bucket, Key=mp_key, PartNumber=1, UploadId=mp["UploadId"], Body=b"x" * (6 * 1024 * 1024)
)
client.complete_multipart_upload(
    Bucket=s.s3_bucket,
    Key=mp_key,
    UploadId=mp["UploadId"],
    MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": part["ETag"]}]},
)
print("Multipart: OK")
client.delete_object(Bucket=s.s3_bucket, Key=key)
client.delete_object(Bucket=s.s3_bucket, Key=mp_key)
print("Delete: OK")
print("PREFLIGHT_OK")
PY
