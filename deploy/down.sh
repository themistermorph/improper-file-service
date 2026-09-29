#!/usr/bin/env bash
# Stoppt den IFS-Stack (als root via sudo). Volumes bleiben erhalten.
set -euo pipefail
cd /home/ifs/ifs
docker compose down
