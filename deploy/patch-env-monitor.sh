#!/usr/bin/env bash
# Ergänzt IFS_SEAWEEDFS_STATUS_URL in der Server-.env (idempotent).
set -uo pipefail
cd /home/ifs/ifs
if ! grep -q '^IFS_SEAWEEDFS_STATUS_URL=' .env; then
  printf 'IFS_SEAWEEDFS_STATUS_URL=http://s3:9333/vol/status\n' >> .env
  echo "env: IFS_SEAWEEDFS_STATUS_URL hinzugefügt"
else
  echo "env: IFS_SEAWEEDFS_STATUS_URL bereits vorhanden"
fi
grep -n '^IFS_SEAWEEDFS_STATUS_URL=' .env
