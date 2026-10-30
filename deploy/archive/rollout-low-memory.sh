#!/usr/bin/env bash
# Aktiviert das Low-Memory-Profil auf dem Testsystem (reine Konfiguration).
set -euo pipefail
cd /home/ifs/ifs

cp -f .env ".env.bak-$(date +%Y%m%d-%H%M%S)"

python3 - <<'PY'
import pathlib

updates = {
    "IFS_S3_MULTIPART_CHUNKSIZE_MB": "8",
    "IFS_S3_TRANSFER_CONCURRENCY": "4",
    "IFS_S3_MAX_POOL_CONNECTIONS": "8",
    "IFS_HTTP_STREAM_CHUNK_BYTES": "2097152",
    "IFS_HTTP_UPLOAD_PART_BYTES": "8388608",
    "IFS_HTTP_UPLOAD_PARALLELISM": "2",
    "IFS_FTP_TRANSFER_BUFFER_BYTES": "131072",
    "IFS_FTP_S3_READAHEAD_BYTES": "2097152",
    "IFS_FTP_FINALIZE_WORKERS": "1",
    "IFS_WORKER_INTERVAL_SECONDS": "30",
    "IFS_WORKER_OUTBOX_BATCH_SIZE": "50",
}
path = pathlib.Path(".env")
lines = path.read_text(encoding="utf-8").splitlines()
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0].strip()
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
print("Aktualisiert:", ", ".join(sorted(updates)))
PY

echo "== Neustart =="
docker compose up -d s3 api worker ftp

for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:8000/healthz; echo

echo "== SeaweedFS-Kommandozeile =="
docker inspect ifs-s3-1 --format '{{join .Config.Cmd " "}}'
echo "== GOMEMLIMIT =="
docker inspect ifs-s3-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^GOMEMLIMIT=' || true

echo "== IFS-Settings (api) =="
docker compose exec -T api python -c \
  "from ifs.config import get_settings; s=get_settings(); print('multipart_mb=',s.s3_multipart_chunksize_mb,'s3_concurrency=',s.s3_transfer_concurrency,'pool=',s.s3_max_pool_connections,'http_chunk=',s.http_stream_chunk_bytes,'part=',s.http_upload_part_bytes,'up_parallel=',s.http_upload_parallelism,'ftp_buf=',s.ftp_transfer_buffer_bytes,'ftp_readahead=',s.ftp_s3_readahead_bytes,'ftp_finalize=',s.ftp_finalize_workers,'worker_interval=',s.worker_interval_seconds,'worker_batch=',s.worker_outbox_batch_size)"

echo "== Container-Status =="
docker compose ps
echo LOW_MEMORY_ROLLOUT_DONE
