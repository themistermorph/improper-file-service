#!/usr/bin/env bash
# Verifiziert IFS gegen SeaweedFS (E2E + Persistenz + Storage-UI-Login).
set -uo pipefail
cd /home/ifs/ifs
BASE="http://127.0.0.1:8000"
CRED=ADMIN_CREDENTIALS.txt
USER="$(grep '^username=' "$CRED" | cut -d= -f2)"
PASS="$(grep '^password=' "$CRED" | cut -d= -f2)"
UIUSER="$(grep '^IFS_S3_UI_USER=' .env | cut -d= -f2)"
UIPASS="$(grep '^IFS_S3_UI_PASSWORD=' .env | cut -d= -f2)"
py() { python3 -c "$1"; }
JSON='Content-Type: application/json'

TOKEN="$(curl -s -X POST "$BASE/api/auth/login" -H "$JSON" -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | py 'import sys,json;print(json.load(sys.stdin)["access_token"])')"
AUTH="Authorization: Bearer $TOKEN"

echo "== E2E: Upload / Download / Range / ZIP / Versionen =="
FOLDER="$(curl -s -X POST "$BASE/api/folders" -H "$AUTH" -H "$JSON" -d '{"name":"seaweed-test"}' | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
EID="$(printf 'hallo-seaweed\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=persist.txt" -H "$AUTH" --data-binary @- | py 'import sys,json;print(json.load(sys.stdin)["id"])')"
echo -n "download: "; curl -s -H "$AUTH" "$BASE/api/entries/$EID/content"
curl -s -o /dev/null -w 'range=%{http_code} (206)\n' -H "$AUTH" -H 'Range: bytes=0-4' "$BASE/api/entries/$EID/content"

XURL="$(curl -s -X POST "$BASE/api/entries/$FOLDER/archive-token" -H "$AUTH" | py 'import sys,json;print(json.load(sys.stdin)["url"])')"
curl -s -o /tmp/seaweed.zip -w 'zip=%{http_code}\n' "$BASE$XURL"
unzip -l /tmp/seaweed.zip | sed -n '4,5p'

printf 'neue version\n' | curl -s -X PUT "$BASE/api/uploads/simple?parent_id=$FOLDER&name=persist.txt" -H "$AUTH" --data-binary @- >/dev/null
curl -s -H "$AUTH" "$BASE/api/entries/$EID/versions" | py 'import sys,json;print("versionen:",[v["seq"] for v in json.load(sys.stdin)])'

echo "== Storage-UI (Basic Auth) =="
curl -s -o /dev/null -w 'ui_ohne_auth=%{http_code} (401)\n' "http://127.0.0.1:9010/"
curl -s -o /dev/null -w 'ui_mit_auth=%{http_code} (200)\n' -u "$UIUSER:$UIPASS" "http://127.0.0.1:9010/"

echo "== Persistenz: s3-Container neu starten, danach erneut lesen =="
docker compose restart s3 >/dev/null 2>&1
HEALTH=""
for _ in $(seq 1 30); do
  HEALTH="$(docker inspect -f '{{.State.Health.Status}}' ifs-s3-1 2>/dev/null)"
  [ "$HEALTH" = "healthy" ] && break
  sleep 3
done
echo "s3 health nach Neustart: $HEALTH"
sleep 3
echo -n "download nach Neustart: "; curl -s -H "$AUTH" "$BASE/api/entries/$EID/content"

echo "== Aufräumen =="
curl -s -X DELETE "$BASE/api/entries/$FOLDER" -H "$AUTH" -o /dev/null
curl -s -X DELETE "$BASE/api/trash/$FOLDER" -H "$AUTH" -o /dev/null
echo VERIFY_SEAWEEDFS_DONE
