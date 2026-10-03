#!/usr/bin/env bash
# Entfernt Testartefakte (ftp-smoke-*, smoke-*) aus der eigenen Wurzel des
# Admin-Kontos: erst in den Papierkorb, dann endgültig löschen.
set -uo pipefail
cd /home/ifs/ifs
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
python3 - "$USER" "$PASS" <<'PY'
import fnmatch
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

patterns = ("ftp-smoke-*", "smoke-*")


def is_smoke(name):
    return any(fnmatch.fnmatch(name, p) for p in patterns)


targets = [e for e in entries if is_smoke(e["name"])]
print("In der Wurzel gefunden:", sorted(e["name"] for e in targets))
for entry in targets:
    req("DELETE", "/api/entries/" + entry["id"], token=token)

# Alles verbliebene (auch zuvor getrashte) endgültig entfernen.
_, trash = req("GET", "/api/trash", token=token)
remaining = [e for e in trash if is_smoke(e["name"])]
print("Im Papierkorb gefunden:", sorted(e["name"] for e in remaining))
purged = 0
for entry in remaining:
    try:
        req("DELETE", "/api/trash/" + entry["id"], token=token)
        purged += 1
    except Exception as exc:  # noqa: BLE001
        print("Purge-Fehler:", entry["name"], exc)
print("Endgueltig geloescht:", purged, "von", len(remaining))
PY
echo CLEANUP_SMOKE_DONE
