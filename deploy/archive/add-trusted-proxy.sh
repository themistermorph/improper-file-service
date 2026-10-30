#!/usr/bin/env bash
# Fügt einen vertrauenswürdigen Proxy zu IFS_TRUSTED_PROXIES in der Server-.env hinzu (idempotent).
set -uo pipefail
cd /home/ifs/ifs
PROXY="${1:-10.13.13.2}"

current="$(grep -E '^IFS_TRUSTED_PROXIES=' .env | cut -d= -f2- || true)"
if [ -z "$current" ]; then
  new="$PROXY"
elif printf '%s' "$current" | tr ',' '\n' | grep -qx "$PROXY"; then
  new="$current"
else
  new="$current,$PROXY"
fi

if grep -q '^IFS_TRUSTED_PROXIES=' .env; then
  sed -i "s|^IFS_TRUSTED_PROXIES=.*|IFS_TRUSTED_PROXIES=${new}|" .env
else
  printf 'IFS_TRUSTED_PROXIES=%s\n' "$new" >> .env
fi

echo "vorher: ${current:-<leer>}"
echo "nachher: $(grep '^IFS_TRUSTED_PROXIES=' .env)"
echo TRUSTED_PROXY_OK
