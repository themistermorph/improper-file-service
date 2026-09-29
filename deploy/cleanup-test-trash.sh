#!/usr/bin/env bash
# Leert den Papierkorb von erkennbaren Testartefakten (lässt Unklares stehen).
set -uo pipefail
cd /home/ifs/ifs
python3 - <<'PY'
import json
import re
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

pattern = re.compile(
    r"^(smoke-|share-verify-|zipjob-|closed-|mini-e2e-|ext-ftp-|ftp-smoke-|bulk-|aktiv-|"
    r"uibulk-|priv-|exfil-|overw-|overwr-|dropchk-|trash-|hardening$|zip-test$|bundle$|"
    r"share-me$|conflict-folder$|quota$|drop$|drop2$|dropzone$|keep$|flood$|meta$|jobs$|"
    r"canc$|upd$|del$|legacy$|team$|shui-|fixcheck-)"
)

items = call("GET", "/api/trash", token=token)
ids = [item["id"] for item in items if pattern.match(item["name"])]
keep = [item["name"] for item in items if not pattern.match(item["name"])]
print(f"vorher: {len(items)} Einträge | Testartefakte: {len(ids)} | unangetastet: {keep}")

if ids:
    result = call("POST", "/api/trash/bulk/purge", {"ids": ids}, token=token)
    print("gelöscht:", result)

after = call("GET", "/api/trash", token=token)
print(f"nachher: {len(after)} Einträge | verbleibend: {[i['name'] for i in after]}")
PY
echo CLEANUP_TEST_TRASH_DONE
