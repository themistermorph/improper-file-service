#!/usr/bin/env bash
# Leert den Papierkorb vollständig (endgültig) und berichtet vorher/nachher.
set -uo pipefail
cd /home/ifs/ifs
python3 - <<'PY'
import json
import urllib.request

BASE = "http://127.0.0.1:8000"
creds = {}
for line in open("ADMIN_CREDENTIALS.txt"):
    key, _, value = line.strip().partition("=")
    creds[key] = value


def call(method, path, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers=headers,
    )
    with urllib.request.urlopen(request) as response:
        body = response.read()
        return json.loads(body) if body else {}


token = call(
    "POST", "/api/auth/login",
    {"username": creds["username"], "password": creds["password"]},
)["access_token"]

before = call("GET", "/api/trash", token=token)
print("vorher:", [item["name"] for item in before])
if before:
    print("geleert:", call("DELETE", "/api/trash", token=token))
after = call("GET", "/api/trash", token=token)
print("nachher:", len(after), [item["name"] for item in after])
PY
echo EMPTY_TRASH_DONE
