#!/usr/bin/env bash
# Verifiziert ZIP-Hintergrund-Jobs: Fortschritt, Download und Abbruch.
set -uo pipefail
cd /home/ifs/ifs
BASE="http://127.0.0.1:8000"
CRED=ADMIN_CREDENTIALS.txt
USER="$(grep '^username=' "$CRED" | cut -d= -f2)"
PASS="$(grep '^password=' "$CRED" | cut -d= -f2)"
py() { python3 -c "$1"; }

TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | py 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
AUTH="Authorization: Bearer $TOKEN"

NAME="zipjob-$$"
FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"name\":\"$NAME\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
curl -s -o /dev/null -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=a.txt" \
  -H "$AUTH" --data-binary 'inhalt a'
SUB="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"parent_id\":\"$FOLDER\",\"name\":\"sub\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
curl -s -o /dev/null -X PUT "$BASE/api/uploads/simple?parent_id=$SUB&name=b.txt" \
  -H "$AUTH" --data-binary 'inhalt b'

echo "== Job starten =="
JOB="$(curl -s -X POST "$BASE/api/archive/jobs" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"entry_ids\":[\"$FOLDER\"]}")"
echo "$JOB" | py 'import sys,json;d=json.load(sys.stdin);print("name:",d["name"],"total_files:",d["total_files"],"total_bytes:",d["total_bytes"])'
JTOKEN="$(echo "$JOB" | py 'import sys,json;print(json.load(sys.stdin)["token"])')"

echo "== Fortschritt pollen =="
for i in $(seq 1 40); do
  ST="$(curl -s "$BASE/api/archive/jobs/$JTOKEN")"
  STATE="$(echo "$ST" | py 'import sys,json;print(json.load(sys.stdin)["status"])')"
  echo "  $ST" | py 'import sys,json;d=json.loads(sys.stdin.read().strip());print("   ",d["status"],d["files_done"],"/",d["total_files"])'
  [ "$STATE" = "ready" ] && break
  [ "$STATE" = "failed" ] && { echo "FEHLER: Job fehlgeschlagen"; exit 1; }
  sleep 0.5
done

echo "== Download =="
CODE="$(curl -s -o /tmp/ifs-job.zip -w '%{http_code}' "$BASE/api/archive/$JTOKEN")"
echo "HTTP: $CODE"
python3 - "$NAME" <<'PY'
import sys, zipfile
with zipfile.ZipFile("/tmp/ifs-job.zip") as z:
    names = sorted(z.namelist())
print("ZIP-Einträge:", names)
expected = {f"{sys.argv[1]}/", f"{sys.argv[1]}/a.txt", f"{sys.argv[1]}/sub/", f"{sys.argv[1]}/sub/b.txt"}
missing = expected - set(names)
assert not missing, f"fehlend: {missing}"
PY

echo "== Abbruch =="
J2="$(curl -s -X POST "$BASE/api/archive/jobs" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"entry_ids\":[\"$FOLDER\"]}")"
J2TOKEN="$(echo "$J2" | py 'import sys,json;print(json.load(sys.stdin)["token"])')"
DEL="$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$BASE/api/archive/jobs/$J2TOKEN")"
GONE="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/archive/jobs/$J2TOKEN")"
echo "DELETE: $DEL (erwartet 204) | Status danach: $GONE (erwartet 404)"

curl -s -o /dev/null -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH"
rm -f /tmp/ifs-job.zip
echo VERIFY_ARCHIVE_JOB_DONE
