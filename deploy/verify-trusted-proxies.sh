#!/usr/bin/env bash
# Verifiziert die Trusted-Proxy-Logik (client_ip / is_secure_request) im api-Container.
set -uo pipefail
cd /home/ifs/ifs
echo "== IFS_TRUSTED_PROXIES =="
docker compose exec -T api python -c "from ifs.config import get_settings; print(get_settings().trusted_proxies)"
echo "== client_ip / is_secure_request =="
docker compose exec -T api python - <<'PY'
from starlette.requests import Request

from ifs.api.deps import client_ip, is_secure_request


def mk(peer, xff=None, xfp=None):
    headers = []
    if xff:
        headers.append((b"x-forwarded-for", xff.encode()))
    if xfp:
        headers.append((b"x-forwarded-proto", xfp.encode()))
    scope = {
        "type": "http", "method": "GET", "path": "/", "query_string": b"",
        "headers": headers, "client": (peer, 1234), "server": ("ifs", 80), "scheme": "http",
    }
    return Request(scope)


cases = [
    ("10.13.13.8", "203.0.113.7"),                 # nginx (10.13.13.0/24) vertraut
    ("172.18.0.1", "203.0.113.7"),                 # Docker-Gateway (172.16.0.0/12) vertraut
    ("10.13.13.5", "203.0.113.7"),                 # LAN-Proxy vertraut
    ("198.51.100.9", "203.0.113.7"),               # nicht vertraut -> Peer-IP
    ("10.13.13.8", "203.0.113.7, 10.13.13.9"),     # vertrauter Hop am Ende wird übersprungen
]
for peer, xff in cases:
    print(f"peer={peer:14} xff={xff!r:32} -> client_ip={client_ip(mk(peer, xff))!r}")
print("security-header https von vertrauenswürdigem Peer:", is_secure_request(mk("10.13.13.8", xfp="https")))
print("security-header https von unbekanntem Peer:       ", is_secure_request(mk("198.51.100.9", xfp="https")))
PY
echo VERIFY_TRUSTED_PROXIES_DONE
