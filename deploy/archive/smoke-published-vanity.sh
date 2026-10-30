#!/usr/bin/env bash
# Smoke-Test: Veröffentlichung mit Vanity-Suffix, 307-Redirect, Galerie, Cleanup.
set -uo pipefail
cd /home/ifs/ifs

USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"

python3 - "$USER" "$PASS" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request

base = "http://127.0.0.1:8000"
username, password = sys.argv[1], sys.argv[2]
stamp = str(time.time_ns())
folder_name = "vanity" + stamp
suffix = "v" + stamp


def call(method, path, *, body=None, headers=None, raw=False):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        if raw:
            data = body
        else:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


for _ in range(30):
    try:
        status, _ = call("GET", "/healthz")
        if status == 200:
            break
    except Exception:
        pass
    time.sleep(0.5)
else:
    raise AssertionError("API nicht erreichbar")

status, payload = call(
    "POST", "/api/auth/login", body={"username": username, "password": password}
)
assert status == 200, (status, payload)
token = json.loads(payload)["access_token"]
auth = {"Authorization": "Bearer " + token}

status, payload = call("POST", "/api/folders", body={"name": folder_name}, headers=auth)
assert status == 201, (status, payload)
folder = json.loads(payload)["id"]

status, payload = call(
    "PUT",
    f"/api/uploads/simple?parent_id={folder}&name=f.txt",
    body=b"vanity",
    headers=auth,
    raw=True,
)
assert status in (200, 201), (status, payload)
entry = json.loads(payload)["id"]

status, payload = call(
    "POST",
    f"/api/published/{entry}",
    body={"public_suffix": suffix},
    headers=auth,
)
assert status == 201, (status, payload)
pub = json.loads(payload)
expected = f"{username}/{suffix}"
assert pub["public_path"] == expected, pub
assert pub["public_suffix"] == suffix, pub

status, payload = call("GET", "/api/published")
assert status == 200
gallery = json.loads(payload)
assert any(
    item["entry_id"] == entry and item["public_path"] == expected for item in gallery
), gallery

status, page = call("GET", "/")
assert status == 200
assert "Eigener Link-Suffix" in page.decode("utf-8", "ignore")
status, _ = call("GET", "/published")
assert status == 200


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


opener = urllib.request.build_opener(NoRedirect)
vanity = f"/published/{username}/{suffix}"
for path in (vanity, f"/published/{username.upper()}/{suffix.upper()}"):
    req = urllib.request.Request(base + path)
    try:
        opener.open(req, timeout=15)
        raise AssertionError("kein Redirect: " + path)
    except urllib.error.HTTPError as exc:
        assert exc.code == 307, (path, exc.code)
        location = exc.headers.get("Location", "")
        assert location.endswith(f"/api/published/{entry}/content"), location

status, payload = call("GET", f"/api/published/{entry}/content")
assert status == 200 and payload == b"vanity", (status, payload)

# Aufräumen
call("DELETE", f"/api/published/{entry}", headers=auth)
call("DELETE", f"/api/entries/{entry}", headers=auth)
call("DELETE", f"/api/entries/{folder}", headers=auth)
print("PUBLISHED_VANITY_OK", vanity)
PY
