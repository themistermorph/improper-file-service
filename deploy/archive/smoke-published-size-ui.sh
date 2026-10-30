#!/usr/bin/env bash
# Smoke: veröffentlichte Ordnergröße + europäische Zeitstempel in der UI.
set -uo pipefail
cd /home/ifs/ifs

USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"

python3 - "$USER" "$PASS" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

base = "http://127.0.0.1:8000"
username, password = sys.argv[1], sys.argv[2]
stamp = str(time.time_ns())
content = b"x" * 12345


def call(method, path, *, body=None, headers=None, raw=False, params=None):
    if params:
        path += "?" + urllib.parse.urlencode(params)
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
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


for _ in range(40):
    try:
        status, _ = call("GET", "/healthz")
        if status == 200:
            break
    except Exception:
        pass
    time.sleep(0.5)
else:
    raise AssertionError("API nicht erreichbar")

status, body = call(
    "POST", "/api/auth/login", body={"username": username, "password": password}
)
assert status == 200, (status, body)
token = json.loads(body)["access_token"]
auth = {"Authorization": "Bearer " + token}

status, body = call(
    "POST", "/api/folders", body={"name": "size-" + stamp}, headers=auth
)
assert status == 201, (status, body)
folder = json.loads(body)["id"]
status, body = call(
    "PUT", "/api/uploads/simple", headers=auth, raw=True, body=content,
    params={"parent_id": folder, "name": "f.bin"},
)
assert status in (200, 201), (status, body)
entry = json.loads(body)["id"]

status, body = call("POST", f"/api/published/{folder}", headers=auth, body={})
assert status == 201, (status, body)
assert json.loads(body)["size"] == len(content), json.loads(body)

status, body = call("GET", "/api/published/mine", headers=auth)
assert status == 200, (status, body)
item = next(row for row in json.loads(body) if row["entry_id"] == folder)
assert item["size"] == len(content), item
print("folder_size_ok", item["size"])

status, page = call("GET", "/")
assert status == 200
html = page.decode("utf-8", "ignore")
assert 'tdSize.textContent = fmtSize(item.size);' in html
assert 'item.type === "folder" ? "ZIP"' not in html
assert "toLocaleString()" not in html
assert "getDate()" in html and "getMonth()" in html and "getFullYear()" in html
print("main_ui_format_ok")

status, gallery = call("GET", "/published")
assert status == 200
gallery_html = gallery.decode("utf-8", "ignore")
assert "toLocaleString()" not in gallery_html
assert "getDate()" in gallery_html and "getMonth()" in gallery_html
print("gallery_ui_format_ok")

call("DELETE", f"/api/published/{folder}", headers=auth)
call("DELETE", f"/api/entries/{entry}", headers=auth)
call("DELETE", f"/api/entries/{folder}", headers=auth)
print("PUBLISHED_SIZE_UI_OK")
PY
