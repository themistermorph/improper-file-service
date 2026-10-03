#!/usr/bin/env bash
# Verifiziert v0.1.5: Benutzer-Anzeigename statt Benutzername in API-Ausgaben.
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
DISPLAY = "Admin Anzeige"


def req(method, path, data=None, token=None, raw=None, content_type="application/json"):
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    if raw is not None:
        headers["Content-Type"] = content_type
        body = raw
    else:
        headers["Content-Type"] = content_type
        body = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=60) as resp:
        payload = resp.read()
        return resp.status, (json.loads(payload) if payload else None)


_, tok = req("POST", "/api/auth/login", {"username": user, "password": pw})
token = tok["access_token"]

# Anzeigename setzen.
req("PATCH", "/api/me", {"display_name": DISPLAY}, token=token)
me = req("GET", "/api/me", token=token)[1]
print("Profil:                 username=%r display_name=%r" % (me["username"], me["display_name"]))

_, root = req("GET", "/api/root", token=token)
folder = req("POST", "/api/folders", {"name": "dn-check", "parent_id": root["id"]}, token=token)[1]
entry = req(
    "PUT",
    "/api/uploads/simple?parent_id=" + folder["id"] + "&name=dn.txt",
    token=token,
    raw=b"inhalt\n",
    content_type="text/plain",
)[1]

req("POST", "/api/published/" + entry["id"], {"public_name": "dn-public.txt"}, token=token)
item = next(i for i in req("GET", "/api/published")[1] if i["entry_id"] == entry["id"])
print("Galerie:                published_by_username=%r published_by_display_name=%r"
      % (item["published_by_username"], item["published_by_display_name"]))
item = next(i for i in req("GET", "/api/published/mine", token=token)[1] if i["entry_id"] == entry["id"])
print("Meine Veroeffentl.:     published_by_username=%r published_by_display_name=%r"
      % (item["published_by_username"], item["published_by_display_name"]))

share = req("POST", "/api/shares", {"entry_id": entry["id"]}, token=token)[1]
sitem = next(s for s in req("GET", "/api/shares", token=token)[1] if s["token"] == share["token"])
print("Freigabe:               created_by_username=%r created_by_display_name=%r"
      % (sitem["created_by_username"], sitem["created_by_display_name"]))

row = req("GET", "/api/audit?action=share.create&limit=10", token=token)[1][0]
print("Audit:                  actor_username=%r actor_display_name=%r"
      % (row["actor_username"], row["actor_display_name"]))

print("== health/version ==")
with urllib.request.urlopen(base + "/version", timeout=10) as resp:
    print(resp.read().decode())

print("== Aufraeumen ==")
req("DELETE", "/api/published/" + entry["id"], token=token)
req("DELETE", "/api/shares/" + share["token"], token=token)
req("DELETE", "/api/entries/" + entry["id"], token=token)
req("DELETE", "/api/trash/" + entry["id"], token=token)
req("DELETE", "/api/entries/" + folder["id"], token=token)
req("DELETE", "/api/trash/" + folder["id"], token=token)
req("PATCH", "/api/me", {"display_name": None}, token=token)
print("Profil zurueckgesetzt:  display_name=%r" % (req("GET", "/api/me", token=token)[1]["display_name"],))
print("VERIFY_DISPLAYNAME_DONE")
PY
