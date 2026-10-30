#!/usr/bin/env bash
# Diagnose: öffentliche Anzeigenamen und Benutzer-Anzeigenamen.
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
me = req("GET", "/api/me", token=token)[1]
print("== /api/me ==")
print("  username=", me["username"], "display_name=", me.get("display_name"))
print("== /api/users ==")
users = req("GET", "/api/users?limit=100", token=token)[1]
for u in users.get("items", []):
    print(f"  {u['username']!r:20} display_name={u.get('display_name')!r:20} admin={u['is_admin']} aktiv={u['is_active']}")
print("== /api/shares (created_by_username) ==")
try:
    shares = req("GET", "/api/shares", token=token)[1]
    for s in shares:
        print(f"  name={s.get('name')!r:25} created_by_username={s.get('created_by_username')!r}")
except Exception as exc:  # noqa: BLE001
    print("  (Shares nicht abrufbar:", exc, ")")
print("DIAG_DISPLAYNAME_DONE")
PY
