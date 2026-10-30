#!/usr/bin/env bash
set -uo pipefail
cd /home/ifs/ifs
echo "== Health =="
curl -fsS http://127.0.0.1:8000/healthz; echo
echo "== Config =="
docker compose exec -T api python -c \
  "from ifs.config import get_settings; s=get_settings(); print('dl_parallel=', s.http_download_parallelism, 'part=', s.http_upload_part_bytes, 'up_parallel=', s.http_upload_parallelism)"
echo "== UI Folder Upload =="
curl -fsS http://127.0.0.1:8000/ | grep -oE 'folderInput|webkitdirectory|uploadEntries|webkitGetAsEntry' | sort -u
echo "== Neue Endpunkte (OpenAPI deaktiviert, daher Route-Check via 401/404) =="
curl -s -o /dev/null -w 'download-token ohne Auth: %{http_code}\n' \
  -X POST http://127.0.0.1:8000/api/entries/00000000-0000-0000-0000-000000000000/download-token
curl -s -o /dev/null -w 'part-upload ohne Auth: %{http_code}\n' \
  -X PUT http://127.0.0.1:8000/api/uploads/00000000-0000-0000-0000-000000000000/parts/1
echo "== Letzte Fehler im API-Log =="
docker compose logs --since=15m --no-color api 2>/dev/null | grep -iE 'traceback|exception|error' | tail -n 20 || true
echo SMOKE_FINAL_DONE
