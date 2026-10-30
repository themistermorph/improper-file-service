#!/usr/bin/env bash
# Baseline-/Statusdiagnose fuer HTTP-Transfers auf dem Testsystem.
set -uo pipefail
cd /home/ifs/ifs

echo "== Version =="
curl -fsS http://127.0.0.1:8000/healthz || true
echo
curl -fsS http://127.0.0.1:8000/version || true
echo

echo "== Container =="
docker compose ps

echo "== Konfiguration (api) =="
docker compose exec -T api python -c \
  "from ifs.config import get_settings; s=get_settings(); print('stream_chunk_default=1048576'); print('s3_pool=', s.s3_max_pool_connections, 'multipart_mb=', s.s3_multipart_chunksize_mb, 'concurrency=', s.s3_transfer_concurrency, 'max_upload=', s.max_upload_size)"

echo "== Freier Speicher =="
df -h / /tmp /var/lib/docker 2>/dev/null || df -h

echo "== Admin-Credentials vorhanden =="
test -f ADMIN_CREDENTIALS.txt && echo yes || echo no
