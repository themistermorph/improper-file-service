#!/usr/bin/env bash
# Live-RCE-Canary (PostgreSQL/Container): Payloads in Namen, Inhalten,
# Templates, ZIPs und FTP dürfen keinen Code ausführen. Markerdateien werden
# in api/ftp/worker und auf dem Host gesucht.
set -uo pipefail
cd /home/ifs/ifs

USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
STAMP="$(date +%s%N)"
MARKER="rce-marker-${STAMP}"
TMP_MARKER="ifs-rce-${STAMP}"

echo "marker=${MARKER} tmp_marker=${TMP_MARKER}"

python3 - "$USER" "$PASS" "$STAMP" "$MARKER" "$TMP_MARKER" <<'PY'
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import ftplib
import ssl

base = "http://127.0.0.1:8000"
username, password, stamp, marker, tmp_marker = sys.argv[1:6]
shell_content = f"#!/bin/sh\ntouch /tmp/{tmp_marker}\n".encode()
payloads = [
    f"$(touch {marker})",
    f"`touch {marker}`",
    f"; touch {marker}; #",
    f'__import__("builtins").open("{marker}","w").write("x")',
    "{{7*7}}",
    f"{{{{ cycler.__init__.__globals__.os.popen('touch {marker}').read() }}}}",
]


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


def clean(label, status, body):
    assert status < 500, (label, status, body[:400])
    text = body.decode("utf-8", "ignore").lower()
    for needle in ("traceback", "subprocess", "system(", "popen(", "jinja2"):
        assert needle not in text, (label, needle, body[:400])


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

# 1) Payload-Namen als Ordner; nichts ausführen.
created_folders = []
status, body = call("POST", "/api/folders", body={"name": "rce-" + stamp}, headers=auth)
assert status == 201, (status, body)
sentinel = json.loads(body)["id"]
for payload in payloads:
    status, body = call("POST", "/api/folders", body={"name": payload}, headers=auth)
    assert status == 201, (payload, status, body)
    # Gleichheit beweist auch für {{7*7}}, dass kein Template gerendert wurde.
    assert json.loads(body)["name"] == payload
    created_folders.append(json.loads(body)["id"])
print("payload_names_created", len(created_folders))

# 2) Datei mit Payload-Namen und Shell-Inhalt; Download bleibt Datum.
file_name = payloads[3]
status, body = call(
    "PUT", "/api/uploads/simple", headers=auth, raw=True, body=shell_content,
    params={"parent_id": sentinel, "name": file_name},
)
assert status in (200, 201), (status, body)
entry = json.loads(body)["id"]
assert json.loads(body)["name"] == file_name
status, body = call("GET", f"/api/entries/{entry}/content", headers=auth)
assert status == 200 and body == shell_content, (status, body)
print("shell_content_stored_literally")

# 3) Suchen/Login/Suffix: Payloads bleiben Daten bzw. werden validiert abgelehnt.
for payload in payloads:
    status, body = call("GET", "/api/users", headers=auth, params={"q": payload})
    assert status == 200, (status, body)
    clean("search", status, body)
    status, body = call(
        "POST", f"/api/published/{entry}", headers=auth,
        body={"public_suffix": payload},
    )
    assert status == 400, (payload, status, body)
    clean("suffix", status, body)
status, body = call(
    "POST", "/api/auth/login",
    body={"username": payloads[3], "password": "irrelevant"},
)
assert status == 401, (status, body)
clean("login", status, body)
print("login_payload=401")

# 4) Zweitordnung: Payload als Benutzername.
payload_user = ("rce" + stamp + "-" + payloads[3])[:200]
status, body = call(
    "POST", "/api/users", headers=auth,
    body={"username": payload_user, "password": "pw-12345678"},
)
assert status == 201, (status, body)
payload_user_id = json.loads(body)["id"]
status, body = call(
    "POST", "/api/auth/login",
    body={"username": payload_user, "password": "pw-12345678"},
)
assert status == 200, (status, body)
print("payload_user_login=200")

# 6) ZIP-Schreibpfad: Payload-Name im Archiv; keine Extraktion.
status, body = call("POST", f"/api/published/{sentinel}", headers=auth)
assert status == 201, (status, body)
status, body = call("GET", f"/api/published/{sentinel}/content")
assert status == 200, (status, body)
with zipfile.ZipFile(io.BytesIO(body)) as zf:
    names = zf.namelist()
assert any(file_name in name for name in names), names
print("zip_names_literal", len(names))

# 7) Path-Traversal-Namen werden abgelehnt.
for bad in ("../escape", "..\\escape", ".."):
    status, body = call("POST", "/api/folders", body={"name": bad}, headers=auth)
    assert status == 400, (bad, status, body)
status, body = call("POST", "/api/folders", body={"name": "..foo"}, headers=auth)
assert status == 201, (status, body)
dotdot_id = json.loads(body)["id"]
print("path_traversal_rejected")

# 8) FTP: Payload-Name + Shell-Inhalt; nichts ausführen.
ftp = ftplib.FTP_TLS(context=ssl._create_unverified_context())
ftp.connect("127.0.0.1", 21, timeout=30)
ftp.login(username, password)
ftp.prot_p()
ftp.set_pasv(True)
ftp_name = f"$(touch${{IFS}}{marker})"
ftp.storbinary(f"STOR {ftp_name}", io.BytesIO(shell_content), blocksize=256 * 1024)
ftp.size(ftp_name)
ftp.delete(ftp_name)
ftp.quit()
print("ftp_payload_stored")

# 9) Cleanup
call("DELETE", f"/api/published/{sentinel}", headers=auth)
call("DELETE", f"/api/entries/{entry}", headers=auth)
call("DELETE", f"/api/entries/{sentinel}", headers=auth)
for folder_id in created_folders:
    call("DELETE", f"/api/entries/{folder_id}", headers=auth)
call("DELETE", f"/api/entries/{dotdot_id}", headers=auth)
call("DELETE", f"/api/users/{payload_user_id}", headers=auth)
print("RCE_PAYLOADS_DONE")
PY

STATUS=$?
if [ "$STATUS" -ne 0 ]; then
    echo "RCE_PAYLOAD_TEST_FAILED status=${STATUS}"
    exit 1
fi

echo "== Marker-Suche =="
FOUND=0
for service in api ftp worker; do
    out="$(docker compose exec -T "$service" sh -c "find /app /tmp /var/lib/ifs -maxdepth 3 \( -name '${MARKER}*' -o -name '${TMP_MARKER}*' \) -print 2>/dev/null" </dev/null || true)"
    if [ -n "$out" ]; then
        echo "RCE_MARKER_FOUND ${service}: $out"
        FOUND=1
    fi
done
if [ -e "/tmp/${TMP_MARKER}" ]; then
    echo "RCE_MARKER_FOUND host: /tmp/${TMP_MARKER}"
    FOUND=1
fi
if [ "$FOUND" -ne 0 ]; then
    exit 1
fi
echo "RCE_NO_MARKER"
echo "RCE_LIVE_DONE"
