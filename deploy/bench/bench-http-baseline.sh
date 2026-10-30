#!/usr/bin/env bash
# HTTP-Baseline: einfacher Upload, Download und Range gegen den laufenden Stack.
# Aufruf auf dem Server: bash deploy/bench-http-baseline.sh
set -uo pipefail
cd /home/ifs/ifs

USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
BASE="http://127.0.0.1:8000"
SIZE_MB="${SIZE_MB:-512}"
FILE="/tmp/ifs-http-bench-${SIZE_MB}m.bin"

json_get() {
  python3 -c "import json,sys; print(json.load(sys.stdin)[\"$1\"])"
}

echo "== Login =="
TOKEN="$(curl -fsS -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | json_get access_token)"
AUTH="Authorization: Bearer $TOKEN"
ROOT="$(curl -fsS -H "$AUTH" "$BASE/api/root" | json_get id)"
echo "root=$ROOT"

if [ ! -f "$FILE" ]; then
  echo "== Testdatei erzeugen (${SIZE_MB} MiB) =="
  dd if=/dev/zero of="$FILE" bs=1M count="$SIZE_MB" status=none
fi
ls -lh "$FILE"

NAME="http-bench-$(date +%s).bin"
cleanup() {
  if [ -n "${ENTRY_ID:-}" ]; then
    curl -sS -o /dev/null -X DELETE -H "$AUTH" "$BASE/api/entries/$ENTRY_ID" || true
  fi
  rm -f "$FILE"
}
trap cleanup EXIT

echo
echo "== Upload: PUT /api/uploads/simple (1 Stream, aktueller Stand) =="
curl -sS -X PUT "$BASE/api/uploads/simple?parent_id=$ROOT&name=$NAME" \
  -H "$AUTH" -H 'Content-Type: application/octet-stream' \
  --data-binary "@$FILE" \
  -o /tmp/ifs-http-upload.json \
  -w 'http=%{http_code} bytes=%{size_upload} time=%{time_total}s rate=%{speed_upload}B/s\n'
ENTRY_ID="$(json_get id < /tmp/ifs-http-upload.json)"
echo "entry=$ENTRY_ID"
curl -fsS -H "$AUTH" "$BASE/api/entries/$ENTRY_ID" | python3 -c \
  'import json,sys; d=json.load(sys.stdin); print("size=",d.get("size"),"sha256=",d.get("sha256"))'

echo
echo "== Download: GET /content (1 Stream, aktueller Stand) =="
for run in 1 2; do
  curl -sS -H "$AUTH" "$BASE/api/entries/$ENTRY_ID/content" -o /dev/null \
    -w "run=$run http=%{http_code} bytes=%{size_download} time=%{time_total}s rate=%{speed_download}B/s\n"
done

echo
echo "== Range: bytes=0-1048575 =="
curl -sS -H "$AUTH" -H 'Range: bytes=0-1048575' \
  -D /tmp/ifs-http-range.headers -o /dev/null \
  "$BASE/api/entries/$ENTRY_ID/content" \
  -w 'http=%{http_code} bytes=%{size_download} time=%{time_total}s\n'
grep -iE '^(HTTP/|content-range|content-length|accept-ranges)' /tmp/ifs-http-range.headers || true

echo
echo "== HEAD =="
curl -sS -I -H "$AUTH" "$BASE/api/entries/$ENTRY_ID/content" | \
  grep -iE '^(HTTP/|content-length|content-type|accept-ranges|etag)' || true

echo
echo "== Fertig =="
