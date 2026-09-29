#!/usr/bin/env bash
# Prüft die Verfügbarkeit der für den Compose-Betrieb benötigten Images.
set -uo pipefail

docker pull -q postgres:16 >/dev/null 2>&1 && echo "OK   postgres:16" || echo "NO   postgres:16"
docker pull -q minio/minio:latest >/dev/null 2>&1 && echo "OK   minio/minio:latest" || echo "NO   minio/minio:latest"
docker pull -q minio/mc:latest >/dev/null 2>&1 && echo "OK   minio/mc:latest" || echo "NO   minio/mc:latest"

for img in \
  adobe/s3mock:latest \
  localstack/localstack:latest \
  quay.io/minio/minio:latest \
  bitnami/minio:latest \
  chrislusf/seaweedfs:latest \
  zenko/cloudserver:latest
do
  if docker manifest inspect "$img" >/dev/null 2>&1; then
    echo "OK   $img"
  else
    echo "NO   $img"
  fi
done
