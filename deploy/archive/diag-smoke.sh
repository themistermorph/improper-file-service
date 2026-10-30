#!/usr/bin/env bash
# Diagnose: verbleibende Smoke-Artefakte in Wurzel und Papierkorb.
set -uo pipefail
cd /home/ifs/ifs
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
python3 - "$USER" "$PASS" <<'PY'
import json
import sys
import urllib.request

base = "http://127.0.0.1:8000"
user, pw = sys.argv[1], sys.argv[2]


def req(method, path, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=60) as resp:
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else None)


_, tok = req("POST", "/api/auth/login", {"username": user, "password": pw})
token = tok["access_token"]
_, root = req("GET", "/api/root", token=token)
_, entries = req("GET", "/api/entries?parent_id=" + root["id"], token=token)
_, trash = req("GET", "/api/trash", token=token)
smoke = lambda name: name.startswith("smoke-") or name.startswith("ftp-smoke-")  # noqa: E731
print("Wurzel:", sorted(e["name"] for e in entries if smoke(e["name"])))
print("Papierkorb:", sorted(e["name"] for e in trash if smoke(e["name"])))
print("Wurzel gesamt:", len(entries), "| Papierkorb gesamt:", len(trash))
PY
echo DIAG_SMOKE_DONE
