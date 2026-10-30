#!/usr/bin/env bash
# Prüft, dass der vertrauenswürdige Proxy im Container aktiv ist.
set -uo pipefail
cd /home/ifs/ifs
echo "== .env =="; grep '^IFS_TRUSTED_PROXIES=' .env
echo "== Container-Env =="; docker compose exec -T api printenv IFS_TRUSTED_PROXIES
echo "== geparste Netze =="; docker compose exec -T api python -c "from ifs.api import deps; print([str(n) for n in deps._trusted_networks()])"
echo "== health/version =="; curl -sS -m5 http://127.0.0.1:8000/healthz; echo; curl -sS -m5 http://127.0.0.1:8000/version; echo
echo VERIFY_TRUSTED_PROXY_DONE
