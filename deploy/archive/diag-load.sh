#!/usr/bin/env bash
set -uo pipefail
cd /home/ifs/ifs
echo "== load =="
uptime
echo "== stats =="
docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}} block={{.BlockIO}} net={{.NetIO}}'
echo "== disk =="
df -h /
echo "== uploads/spool =="
docker compose exec -T api sh -c 'ls -1 /var/lib/ifs/spool | wc -l; du -sh /var/lib/ifs/spool'
echo "== env parallel =="
docker compose exec -T api python -c "from ifs.config import get_settings; print(get_settings().http_download_parallelism)" 2>/dev/null
echo DIAG_DONE
