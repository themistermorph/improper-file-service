#!/usr/bin/env bash
# Rollout-Verifikation HTTP: Config, UI, echter Part-Upload, Download-Ticket.
set -uo pipefail
cd /home/ifs/ifs

USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
export IFS_USER="$USER" IFS_PASS="$PASS"

echo "== Health/Version =="
curl -fsS http://127.0.0.1:8000/healthz; echo
curl -fsS http://127.0.0.1:8000/version; echo

echo "== Neue HTTP-Konfiguration (api) =="
docker compose exec -T api python -c \
  "from ifs.config import get_settings; s=get_settings(); print('stream=', s.http_stream_chunk_bytes, 'dl_parallel=', s.http_download_parallelism, 'token_ttl=', s.http_download_token_ttl_seconds, 'part=', s.http_upload_part_bytes, 'up_parallel=', s.http_upload_parallelism)"

echo "== Web-UI enthält Ordner-Upload =="
curl -fsS http://127.0.0.1:8000/ | grep -oE 'folderInput|webkitdirectory|uploadEntries|webkitGetAsEntry' | sort -u

echo "== Echter Part-Upload + Download-Ticket =="
python3 - <<'PY'
import hashlib
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "http://127.0.0.1:8000"
SIZE = 128 * 1024 * 1024
PATH = "/tmp/ifs-http-rollout-parts.bin"


def request(method, path, data=None, headers=None):
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(req, timeout=900) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {path} -> {exc.code}: {body}") from exc


def json_request(method, path, payload=None, token=None):
    headers = {}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    _status, _headers, body = request(method, path, data, headers)
    return json.loads(body) if body else None


print("login ...")
login = json_request(
    "POST", "/api/auth/login",
    {"username": os.environ["IFS_USER"], "password": os.environ["IFS_PASS"]},
)
token = login["access_token"]
auth = {"Authorization": "Bearer " + token}

root = json_request("GET", "/api/root", token=token)["id"]
stamp = int(time.time())
folder = json_request(
    "POST", "/api/folders", {"name": f"http-rollout-{stamp}"}, token
)["id"]
nested = json_request(
    "POST", "/api/folders", {"parent_id": folder, "name": "unterordner"}, token
)["id"]

with open(PATH, "wb") as handle:
    remaining = SIZE
    while remaining:
        chunk = os.urandom(min(8 * 1024 * 1024, remaining))
        handle.write(chunk)
        remaining -= len(chunk)
digest = hashlib.sha256()
with open(PATH, "rb") as handle:
    while True:
        chunk = handle.read(4 * 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
sha = digest.hexdigest()

session = json_request(
    "POST", "/api/uploads",
    {"parent_id": nested, "name": "parts.bin", "size": SIZE,
     "sha256": sha, "parallel": True},
    token,
)
print("session:", {k: session.get(k) for k in ("id", "offset", "part_size", "parallelism")})
upload_id = session["id"]
part_size = session["part_size"]
parts = (SIZE + part_size - 1) // part_size
part_times = []


def put_part(number):
    start = (number - 1) * part_size
    with open(PATH, "rb") as handle:
        handle.seek(start)
        data = handle.read(part_size)
    t0 = time.perf_counter()
    request(
        "PUT", f"/api/uploads/{upload_id}/parts/{number}", data,
        {**auth, "Content-Type": "application/octet-stream"},
    )
    return time.perf_counter() - t0


t0 = time.perf_counter()
with ThreadPoolExecutor(max_workers=4) as pool:
    part_times = list(pool.map(put_part, range(1, parts + 1)))
part_elapsed = time.perf_counter() - t0
print(f"parts: {parts} x {part_size} bytes, parallel=4, {SIZE/part_elapsed/1e6:.1f} MB/s ({part_elapsed:.2f}s)")

status, headers, _ = request("HEAD", f"/api/uploads/{upload_id}", headers=auth)
print("head:", status, "offset=", headers.get("Upload-Offset"),
      "parts=", headers.get("Upload-Parts"), "part_size=", headers.get("Upload-Part-Size"))

t0 = time.perf_counter()
result = json_request("POST", f"/api/uploads/{upload_id}/complete", token=token)
complete_elapsed = time.perf_counter() - t0
print(f"complete: {complete_elapsed:.2f}s ({SIZE/complete_elapsed/1e6:.1f} MB/s), "
      f"total: {part_elapsed+complete_elapsed:.2f}s ({SIZE/(part_elapsed+complete_elapsed)/1e6:.1f} MB/s)")
print("complete:", {k: result.get(k) for k in ("name", "size", "sha256")})
assert result["size"] == SIZE and result["sha256"] == sha

listing = json_request("GET", f"/api/entries?parent_id={nested}", token=token)
print("listing nested:", [(entry["name"], entry["size"]) for entry in listing])
assert any(entry["name"] == "parts.bin" and entry["size"] == SIZE for entry in listing)

ticket = json_request("POST", f"/api/entries/{result['id']}/download-token", token=token)
url = ticket["url"]
status, headers, _ = request("GET", url, headers={"Range": "bytes=0-1048575"})
print("ticket range:", status, headers.get("Content-Range"), "len=", headers.get("Content-Length"))
assert status == 206 and headers.get("Content-Range") == f"bytes 0-1048575/{SIZE}"

t0 = time.perf_counter()
req = urllib.request.Request(BASE + url)
with urllib.request.urlopen(req, timeout=900) as response:
    got = 0
    while True:
        chunk = response.read(4 * 1024 * 1024)
        if not chunk:
            break
        got += len(chunk)
elapsed = time.perf_counter() - t0
print(f"ticket download: {got} bytes, {got/elapsed/1e6:.1f} MB/s ({elapsed:.2f}s)")
assert got == SIZE

# Aufräumen (Papierkorb; der Worker räumt später endgültig auf).
request("DELETE", f"/api/entries/{result['id']}", headers=auth)
request("DELETE", f"/api/entries/{folder}", headers=auth)
os.unlink(PATH)
print("PART_ROLLOUT_OK")
PY

echo "== API-Fehler seit dem Neustart =="
docker compose logs --since=10m --no-color api 2>/dev/null | \
  grep -iE "traceback|exception|error" | tail -n 30 || true
