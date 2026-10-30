#!/usr/bin/env bash
# Setzt die vertrauenswürdigen Proxy-Netze in der Server-.env und erzeugt `api` neu.
set -euo pipefail
cd /home/ifs/ifs
VALUE="172.16.0.0/12,10.13.13.0/24"
if grep -q '^IFS_TRUSTED_PROXIES=' .env; then
  sed -i "s|^IFS_TRUSTED_PROXIES=.*|IFS_TRUSTED_PROXIES=${VALUE}|" .env
else
  echo "IFS_TRUSTED_PROXIES=${VALUE}" >> .env
fi
echo "== .env =="
grep '^IFS_TRUSTED_PROXIES=' .env
echo "== api neu erzeugen =="
docker compose up -d api
echo "== Settings im Container =="
docker compose exec -T api python -c "from ifs.config import get_settings; print(get_settings().trusted_proxies)"
echo SET_TRUSTED_PROXIES_DONE
