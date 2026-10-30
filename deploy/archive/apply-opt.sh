#!/usr/bin/env bash
# Wendet die Build-Optimierung an (Container neu erstellen, alte Images aufräumen).
set -uo pipefail
cd /home/ifs/ifs
echo "== up -d =="
docker compose up -d
echo "== alte Einzel-Images entfernen =="
docker rmi ifs-api:latest ifs-ftp:latest ifs-worker:latest 2>/dev/null || true
echo "== ps =="
docker compose ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'
echo "== health/version =="
curl -sS -m5 http://127.0.0.1:8000/healthz; echo
curl -sS -m5 http://127.0.0.1:8000/version; echo
echo "== images =="
docker images --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep -E 'ifs' || true
echo APPLY_OPT_DONE
