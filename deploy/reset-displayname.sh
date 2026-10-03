#!/usr/bin/env bash
# Setzt den Anzeigenamen des Admin-Kontos zurück (leerer String = löschen).
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
        return json.loads(raw) if raw else None


token = req("POST", "/api/auth/login", {"username": user, "password": pw})["access_token"]
req("PATCH", "/api/me", {"display_name": ""}, token=token)
me = req("GET", "/api/me", token=token)
print("admin display_name =", repr(me["display_name"]))
PY
echo RESET_DISPLAYNAME_DONE
