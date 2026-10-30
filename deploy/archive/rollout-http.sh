#!/usr/bin/env bash
set -euo pipefail
cd /home/ifs/ifs
echo "== Build =="
docker compose build
echo "== Up =="
docker compose up -d api worker ftp
echo "== Status =="
docker compose ps
