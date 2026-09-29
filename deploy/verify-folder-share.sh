#!/usr/bin/env bash
# Verifiziert Ordner-Freigaben: ZIP-Download (curl -O) und Upload per Link (curl -T).
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

NAME="share-verify-$$"
FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"name\":\"$NAME\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
curl -s -o /dev/null -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=a.txt" \
  -H "$AUTH" --data-binary 'inhalt a'
SUB="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"parent_id\":\"$FOLDER\",\"name\":\"sub\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
curl -s -o /dev/null -X PUT "$BASE/api/uploads/simple?parent_id=$SUB&name=b.txt" \
  -H "$AUTH" --data-binary 'inhalt b'

STOKEN="$(curl -s -X POST "$BASE/api/shares" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"entry_id\":\"$FOLDER\",\"allow_upload\":true}" | py 'import sys,json;print(json.load(sys.stdin)["token"])')"
echo "Share-Token: $STOKEN (allow_upload=true)"

echo "== 1) Download mit curl -O =="
rm -rf /tmp/ifs-dl && mkdir -p /tmp/ifs-dl
( cd /tmp/ifs-dl && curl -s -O "$BASE/api/shares/$STOKEN/content/$NAME.zip" )
ls -1 /tmp/ifs-dl
ZIP="$(ls -1 /tmp/ifs-dl/*.zip 2>/dev/null | head -n1)"
if [ -z "$ZIP" ]; then echo "FEHLER: curl -O hat keine .zip gespeichert"; else
  echo "curl -O Datei: $(basename "$ZIP")"
  python3 - "$ZIP" "$NAME" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    names = sorted(z.namelist())
print("ZIP-Einträge:", names)
expected = {f"{sys.argv[2]}/", f"{sys.argv[2]}/a.txt", f"{sys.argv[2]}/sub/", f"{sys.argv[2]}/sub/b.txt"}
missing = expected - set(names)
assert not missing, f"fehlend: {missing}"
PY
fi

echo "== 2) Upload per Link (curl -T / PUT) =="
printf 'hochgeladen' > /tmp/ifs-drop.txt
curl -s -T /tmp/ifs-drop.txt "$BASE/api/shares/$STOKEN/upload?name=drop.txt" \
  | py 'import sys,json;d=json.load(sys.stdin);print("neu:",d["name"],d["size"],"B")'
curl -s -T /tmp/ifs-drop.txt "$BASE/api/shares/$STOKEN/upload?name=drop.txt" \
  | py 'import sys,json;print("Konflikt ->",json.load(sys.stdin)["name"])'
curl -s -X POST -T /tmp/ifs-drop.txt "$BASE/api/shares/$STOKEN/upload?name=drop.txt&overwrite=true" \
  | py 'import sys,json;print("Request-Flag ohne Freigabe-Eigenschaft ->",json.load(sys.stdin)["name"])'
curl -s -X PATCH "$BASE/api/shares/$STOKEN" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"overwrite":true}' >/dev/null
curl -s -T /tmp/ifs-drop.txt "$BASE/api/shares/$STOKEN/upload?name=drop.txt" \
  | py 'import sys,json;print("overwrite (Freigabe) ->",json.load(sys.stdin)["name"])'

echo "== 3) Upload ohne allow_upload wird abgelehnt =="
F2="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"name\":\"closed-$$\"}" | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
T2="$(curl -s -X POST "$BASE/api/shares" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"entry_id\":\"$F2\"}" | py 'import sys,json;print(json.load(sys.stdin)["token"])')"
CODE="$(curl -s -o /dev/null -w '%{http_code}' -T /tmp/ifs-drop.txt "$BASE/api/shares/$T2/upload?name=x.txt")"
echo "HTTP ohne allow_upload: $CODE (erwartet 403)"

echo "== 4) Datei in ZIP nach Upload =="
curl -s "$BASE/api/shares/$STOKEN/content" -o /tmp/ifs-after.zip
python3 - <<'PY'
import zipfile
with zipfile.ZipFile("/tmp/ifs-after.zip") as z:
    names = sorted(z.namelist())
print("ZIP enthält drop.txt:", any(n.endswith("drop.txt") for n in names))
print("ZIP enthält drop (2).txt:", any("drop (2).txt" in n for n in names))
PY

curl -s -o /dev/null -X DELETE "$BASE/api/shares/$STOKEN" -H "$AUTH"
curl -s -o /dev/null -X DELETE "$BASE/api/shares/$T2" -H "$AUTH"
curl -s -o /dev/null -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH"
curl -s -o /dev/null -X DELETE "$BASE/api/entries/$F2" -H "$AUTH"
rm -rf /tmp/ifs-dl /tmp/ifs-drop.txt /tmp/ifs-after.zip
echo VERIFY_FOLDER_SHARE_DONE
