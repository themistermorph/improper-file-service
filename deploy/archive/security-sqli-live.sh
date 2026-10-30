#!/usr/bin/env bash
# Live-SQLi-Canary gegen PostgreSQL (Testhost).
# Destruktive, Union-, Zeit- und Wildcard-Payloads über alle Eingabeklassen;
# danach müssen Tabellen, Sentinel und API unverändert funktionieren.
set -uo pipefail
cd /home/ifs/ifs

USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"

count() {
    docker compose exec -T db psql -U ifs -d ifs -tAc "$1" </dev/null | tr -d '[:space:]'
}

ENTRIES_BEFORE="$(count 'SELECT count(*) FROM entries')"
USERS_BEFORE="$(count 'SELECT count(*) FROM users')"
PUB_BEFORE="$(count 'SELECT count(*) FROM published_entries')"
echo "before entries=$ENTRIES_BEFORE users=$USERS_BEFORE published=$PUB_BEFORE"

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
payloads = [
    "' OR '1'='1",
    "' OR 1=1 --",
    "'; DROP TABLE entries; --",
    "'; DROP TABLE users; --",
    "'; DROP TABLE published_entries; --",
    '" OR 1=1 --',
    "1 UNION SELECT username, password_hash FROM users --",
    "' AND (SELECT pg_sleep(3)) IS NULL --",
    "admin'--",
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


def clean(label, status, payload):
    assert status < 500, (label, status, payload[:400])
    text = payload.decode("utf-8", "ignore").lower()
    for needle in (
        "syntax error",
        "psycopg",
        "sqlalchemy",
        "traceback",
        "programmingerror",
        "operationalerror",
        "unterminated",
    ):
        assert needle not in text, (label, needle, payload[:400])


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

# 1) Auth-Bypass-Versuch (ein Fehlversuch, unter dem Rate-Limit)
status, body = call(
    "POST", "/api/auth/login",
    body={"username": "admin' OR '1'='1", "password": "wrong"},
)
assert status == 401, (status, body)
clean("login-bypass", status, body)
print("login_bypass=401")

# 2) Sentinel + destruktiver Eintragsname
status, body = call(
    "POST", "/api/folders", body={"name": "sentinel-" + stamp}, headers=auth
)
assert status == 201, (status, body)
sentinel = json.loads(body)["id"]

payload_folder_name = "sqli'; DROP TABLE entries; --" + stamp
status, body = call(
    "POST", "/api/folders",
    body={"name": payload_folder_name}, headers=auth,
)
assert status == 201, (status, body)
payload_folder = json.loads(body)["id"]
assert json.loads(body)["name"] == payload_folder_name

status, body = call(
    "PUT", "/api/uploads/simple", headers=auth, raw=True, body=b"sentinel-data",
    params={"parent_id": sentinel, "name": "' OR 1=1 --.txt"},
)
assert status in (200, 201), (status, body)
sentinel_file = json.loads(body)["id"]
print("payload_folder_created", payload_folder)

# 3) Zweitordnung: Payload als Benutzername
payload_user = "sqli' OR '1'='1 --" + stamp
status, body = call(
    "POST", "/api/users", headers=auth,
    body={"username": payload_user, "password": "pw-12345678"},
)
assert status == 201, (status, body)
payload_user_id = json.loads(body)["id"]
assert json.loads(body)["username"] == payload_user
status, body = call(
    "POST", "/api/auth/login",
    body={"username": payload_user, "password": "pw-12345678"},
)
assert status == 200, (status, body)
print("payload_user_login=200")

# 4) Suchen/Filter (gebundene Parameter; kein OR/Union-Treffer)
status, body = call("GET", "/api/users", headers=auth, params={"q": "' OR 1=1 --"})
assert status == 200, (status, body)
clean("users-q", status, body)
status, body = call("GET", "/api/users", headers=auth, params={"q": "'; DROP TABLE users; --"})
assert status == 200, (status, body)
clean("users-drop", status, body)

status, body = call(
    "GET", "/api/audit", headers=auth,
    params={"action": "'; DROP TABLE audit_log; --"},
)
assert status == 200, (status, body)
clean("audit-action", status, body)

# 5) Veröffentlichung + Suche + Redirect
status, body = call(
    "POST", f"/api/published/{sentinel_file}", headers=auth,
    body={"public_suffix": "sqli" + stamp},
)
assert status == 201, (status, body)
pub = json.loads(body)
assert pub["public_path"] == f"{username}/sqli{stamp}", pub
for q in ("%", "_", "\\", "' OR 1=1 --", "'; DROP TABLE published_entries; --"):
    status, body = call("GET", "/api/published", params={"q": q})
    assert status == 200, (q, status, body)
    clean("published-q", status, body)
status, body = call(
    "POST", f"/api/published/{sentinel_file}", headers=auth,
    body={"public_suffix": "'; DROP TABLE published_entries; --"},
)
assert status == 400, (status, body)
clean("published-suffix", status, body)
status, body = call(
    "GET",
    "/published/" + username + "/" + urllib.parse.quote("' OR 1=1 --", safe=""),
)
assert status == 404, (status, body)
clean("published-redirect", status, body)

# 6) Zeitbasiert: pg_sleep darf nicht ausgeführt werden
start = time.perf_counter()
status, body = call(
    "GET", "/api/users", headers=auth,
    params={"q": "' AND (SELECT pg_sleep(3)) IS NULL --"},
)
elapsed = time.perf_counter() - start
assert status == 200, (status, body)
clean("time-based", status, body)
assert elapsed < 2.5, elapsed
print(f"time_based_elapsed={elapsed:.3f}s")

# 7) UUID / Token / Pagination
for payload in payloads:
    encoded = urllib.parse.quote(payload, safe="")
    status, body = call("GET", f"/api/entries/{encoded}", headers=auth)
    assert status == 422, (payload, status, body)
    clean("uuid", status, body)
    status, body = call("GET", f"/api/shares/{encoded}/content")
    assert status in (404, 422), (payload, status, body)
    clean("share-token", status, body)
    status, body = call("GET", f"/api/preview/{encoded}")
    assert status in (401, 404, 422), (payload, status, body)
    clean("preview-token", status, body)
for params in ({"limit": "' OR 1=1 --"}, {"offset": "1; DROP TABLE users; --"}):
    status, body = call("GET", "/api/users", headers=auth, params=params)
    assert status == 422, (params, status, body)
    clean("pagination", status, body)

# 8) Kanarienvogel: Sentinel und Tabellen leben
status, body = call("GET", "/api/entries", headers=auth)
assert status == 200, (status, body)
root_names = {item["name"] for item in json.loads(body)}
assert "sentinel-" + stamp in root_names, root_names
status, body = call("GET", f"/api/entries/{payload_folder}", headers=auth)
assert status == 200, (status, body)
status, body = call("GET", f"/api/entries/{sentinel_file}/content", headers=auth)
assert status == 200 and body == b"sentinel-data", (status, body)
status, body = call("GET", "/api/published")
assert status == 200, (status, body)
assert any(
    item["public_path"] == f"{username}/sqli{stamp}" for item in json.loads(body)
)

# 9) Aufräumen
call("DELETE", f"/api/published/{sentinel_file}", headers=auth)
call("DELETE", f"/api/entries/{sentinel_file}", headers=auth)
call("DELETE", f"/api/entries/{payload_folder}", headers=auth)
call("DELETE", f"/api/entries/{sentinel}", headers=auth)
call("DELETE", f"/api/users/{payload_user_id}", headers=auth)
print("SQLI_LIVE_OK")
PY

ENTRIES_AFTER="$(count 'SELECT count(*) FROM entries')"
USERS_AFTER="$(count 'SELECT count(*) FROM users')"
PUB_AFTER="$(count 'SELECT count(*) FROM published_entries')"
echo "after entries=$ENTRIES_AFTER users=$USERS_AFTER published=$PUB_AFTER"

for table in entries users published_entries audit_log; do
    exists="$(count "SELECT to_regclass('public.${table}') IS NOT NULL")"
    echo "table_${table}=${exists}"
    test "$exists" = "t"
done

echo "== API-Log: SQL-Fehler? =="
matches="$(docker compose logs --since 10m api </dev/null 2>&1 | grep -iE 'syntax error|programmingerror|operationalerror|psycopg.*error|unterminated' || true)"
if [ -n "$matches" ]; then
    echo "SQL_ERROR_LOG_FOUND"
    echo "$matches" | tail -n 10
    exit 1
fi
echo "SQLI_LIVE_DONE"
