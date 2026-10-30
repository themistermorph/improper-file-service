#!/usr/bin/env bash
# Prüft die aktive Finalisierungs-Konfiguration und die Worker-Threads.
set -uo pipefail
cd /home/ifs/ifs

docker compose exec -T ftp python -c \
    "from ifs.config import get_settings; print('ftp_finalize_workers =', get_settings().ftp_finalize_workers)"

echo "== Threads =="
docker compose exec -T ftp sh -c \
    "ps -T -o tid,comm 2>/dev/null | grep -i 'ifs-ftp' | head -5" || true

echo "== Log =="
docker compose logs --tail 200 ftp 2>&1 | grep -i "Finalisierungs-Worker" | tail -3 || true
