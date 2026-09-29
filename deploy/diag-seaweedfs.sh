#!/usr/bin/env bash
# Diagnose SeaweedFS-Container.
set -uo pipefail
cd /home/ifs/ifs

docker inspect -f 'status={{.State.Status}} restarts={{.RestartCount}} health={{.State.Health.Status}}' ifs-s3-1

echo "--- Ports im Container ---"
docker compose exec -T s3 sh -c '
  curl -s -o /dev/null -w "filer_http=%{http_code}\n" http://localhost:8888/ || echo filer_curl_fail
  curl -s -o /dev/null -w "s3_http=%{http_code}\n" http://localhost:8333/ || echo s3_curl_fail
  wget -q -O /dev/null http://localhost:8888/ && echo WGET_OK || echo WGET_FAIL
'

echo "--- Healthcheck-Log ---"
docker inspect -f '{{range .State.Health.Log}}{{.ExitCode}} {{.Output}}{{end}}' ifs-s3-1 | head -c 400
echo
echo "--- Panic/Exit ---"
docker compose logs s3 2>&1 | grep -iE 'panic|fatal|exit status|shutdown' | tail -n 10
