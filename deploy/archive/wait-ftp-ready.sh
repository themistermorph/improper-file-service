#!/usr/bin/env bash
# Wartet, bis der FTPS-Control-Port erreichbar ist, und zeigt den Dienststatus.
set -uo pipefail
cd /home/ifs/ifs

for i in $(seq 1 40); do
    if (exec 3<>/dev/tcp/127.0.0.1/21) 2>/dev/null; then
        exec 3>&-
        echo "FTP_READY_AFTER_${i}s"
        sleep 3
        docker compose ps --format 'table {{.Service}}\t{{.Status}}'
        exit 0
    fi
    sleep 1
done

echo "FTP_NOT_READY"
docker compose logs --tail 30 ftp
exit 1
