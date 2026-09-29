#!/usr/bin/env bash
# Probe: SeaweedFS-Status-Endpunkte im Container.
set -uo pipefail
cd /home/ifs/ifs
docker compose exec -T s3 sh -c '
  echo "--- /cluster/status ---"; curl -s http://localhost:9333/cluster/status | head -c 500; echo
  echo "--- /vol/status ---";    curl -s http://localhost:9333/vol/status    | head -c 1200; echo
  echo "--- /dir/status ---";    curl -s http://localhost:9333/dir/status    | head -c 400; echo
  echo "--- s3 metrics ---";     curl -s http://localhost:8333/metrics 2>/dev/null | head -c 200; echo
'
