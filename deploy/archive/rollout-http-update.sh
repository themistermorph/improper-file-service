#!/usr/bin/env bash
set -euo pipefail
cd /home/ifs/ifs
python3 - <<'PY'
import pathlib
path = pathlib.Path(".env")
lines = []
if path.exists():
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("IFS_HTTP_DOWNLOAD_PARALLELISM="):
            lines.append(line)
lines.append("IFS_HTTP_DOWNLOAD_PARALLELISM=1")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
docker compose build
docker compose up -d api worker ftp
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:8000/healthz; echo
docker compose exec -T api python -c \
  "from ifs.config import get_settings; s=get_settings(); print('dl_parallel=', s.http_download_parallelism, 'stream=', s.http_stream_chunk_bytes, 'part=', s.http_upload_part_bytes)"
echo ROLLOUT_UPDATE_DONE
