#!/usr/bin/env bash
# Ergänzt IFS_DB_PASSWORD in der Server-.env (idempotent), passend zum neuen Compose.
set -uo pipefail
cd /home/ifs/ifs
if ! grep -q '^IFS_DB_PASSWORD=' .env; then
  printf 'IFS_DB_PASSWORD=ifs\n' >> .env
  echo "env: IFS_DB_PASSWORD hinzugefügt"
else
  echo "env: IFS_DB_PASSWORD bereits vorhanden"
fi
