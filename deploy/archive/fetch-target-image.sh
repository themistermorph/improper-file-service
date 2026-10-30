#!/usr/bin/env bash
# Lädt ein Bild anhand der Entry-ID (öffentlich veröffentlicht oder als Admin-Eintrag).
set -uo pipefail
cd /home/ifs/ifs

USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
ENTRY="01a17f51-c675-7603-bb26-fc29b0934f42"

python3 - "$USER" "$PASS" "$ENTRY" <<'PY'
import json
import sys
import urllib.error
import urllib.request

base = "http://127.0.0.1:8000"
username, password, entry = sys.argv[1:4]


def get(path, headers=None):
    req = urllib.request.Request(base + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.headers.get("content-type"), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("content-type"), exc.read()


def save(content_type, data):
    with open("/tmp/ifs-target-image", "wb") as handle:
        handle.write(data)
    with open("/tmp/ifs-target-image.meta", "w", encoding="utf-8") as handle:
        handle.write((content_type or "application/octet-stream") + "\n")
    print("saved bytes=", len(data), "type=", content_type)


status, content_type, data = get(f"/api/published/{entry}/content")
print("published:", status, content_type, len(data))
if status == 200:
    save(content_type, data)
    raise SystemExit(0)

request = urllib.request.Request(
    base + "/api/auth/login",
    data=json.dumps({"username": username, "password": password}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        token = json.load(response)["access_token"]
except Exception as exc:
    print("login failed:", exc)
    raise SystemExit(2)

auth = {"Authorization": "Bearer " + token}
status, content_type, data = get(f"/api/entries/{entry}", headers=auth)
print("entry-meta:", status, content_type, data[:300].decode("utf-8", "ignore"))
if status != 200:
    print("TARGET_IMAGE_NO_ACCESS")
    raise SystemExit(3)

status, content_type, data = get(f"/api/entries/{entry}/content", headers=auth)
print("entry-content:", status, content_type, len(data))
if status != 200:
    print("TARGET_IMAGE_NO_ACCESS")
    raise SystemExit(3)
save(content_type, data)
PY
