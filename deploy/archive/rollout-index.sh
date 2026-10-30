#!/usr/bin/env bash
# Rollt ausschließlich die Web-UI (src/ifs/web/index.html) aus:
# Image neu bauen (index.html ist Package-Data) und `api` neu erzeugen.
set -euo pipefail
cd /home/ifs/ifs
echo "== build =="
docker compose build
echo "== up api =="
docker compose up -d api
sleep 3
echo "== sha256 index.html (Datei auf dem Server) =="
sha256sum src/ifs/web/index.html
echo "== sha256 ausgeliefertes / =="
curl -sS -m5 http://127.0.0.1:8000/ | sha256sum
echo "== version/health =="
curl -sS -m5 http://127.0.0.1:8000/version; echo
curl -sS -m5 http://127.0.0.1:8000/healthz; echo
echo ROLLOUT_INDEX_DONE
