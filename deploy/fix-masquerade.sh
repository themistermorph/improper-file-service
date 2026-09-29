#!/usr/bin/env bash
# Setzt die Masquerade-Adresse für FTPS (als Benutzer ifs, ändert nur .env).
set -euo pipefail
cd /home/ifs/ifs
ADDR="${IFS_FTP_MASQUERADE_ADDRESS:-${1:-}}"
if [ -z "$ADDR" ]; then
  echo "Aufruf: fix-masquerade.sh <host-oder-ip>  (oder IFS_FTP_MASQUERADE_ADDRESS setzen)" >&2
  exit 2
fi
if grep -q '^IFS_FTP_MASQUERADE_ADDRESS=' .env; then
  sed -i "s|^IFS_FTP_MASQUERADE_ADDRESS=.*|IFS_FTP_MASQUERADE_ADDRESS=${ADDR}|" .env
else
  echo "IFS_FTP_MASQUERADE_ADDRESS=${ADDR}" >> .env
fi
grep '^IFS_FTP_MASQUERADE_ADDRESS=' .env
echo "ENV_OK"
