#!/usr/bin/env bash
# Prüft nach dem Web-UI-Rollout: Container-Status, Logs, Version/Health und liefert die Seite aus.
set -uo pipefail
cd /home/ifs/ifs
echo "== ps =="
docker compose ps
echo "== logs api (tail 30) =="
docker compose logs --tail=30 api 2>&1 | tail -n 30
echo "== warten + healthz =="
for i in $(seq 1 15); do
  code="$(curl -s -o /dev/null -w '%{http_code}' -m5 http://127.0.0.1:8000/healthz || true)"
  echo "try $i: healthz=$code"
  if [ "$code" = "200" ]; then break; fi
  sleep 2
done
echo "== version =="; curl -sS -m5 http://127.0.0.1:8000/version; echo
echo "== sha256 / =="; curl -sS -m10 http://127.0.0.1:8000/ | sha256sum
echo CHECK_INDEX_DONE
