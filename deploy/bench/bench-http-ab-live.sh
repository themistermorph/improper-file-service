#!/usr/bin/env bash
# Kontrolliertes HTTP-Download-A/B auf dem Testsystem (gleiche Datei, warm).
set -uo pipefail
cd /home/ifs/ifs
USER="$(grep '^username=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
PASS="$(grep '^password=' ADMIN_CREDENTIALS.txt | cut -d= -f2)"
BASE="http://127.0.0.1:8000"
FILE=/tmp/ifs-ab-256m.bin
SIZE_MB=256

json_get() { python3 -c "import json,sys; print(json.load(sys.stdin)[\"$1\"])"; }

set_env() {
  python3 - "$1" <<'PY'
import pathlib, sys
value = sys.argv[1]
path = pathlib.Path(".env")
lines = []
if path.exists():
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("IFS_HTTP_DOWNLOAD_PARALLELISM="):
            lines.append(line)
lines.append(f"IFS_HTTP_DOWNLOAD_PARALLELISM={value}")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

wait_ready() {
  for _ in $(seq 1 60); do
    if curl -fsS "$BASE/healthz" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "API nicht bereit" >&2
  return 1
}

if [ ! -f "$FILE" ]; then
  echo "== Testdatei ${SIZE_MB} MiB (random) =="
  python3 - "$FILE" "$SIZE_MB" <<'PY'
import os, sys
path, mb = sys.argv[1], int(sys.argv[2])
with open(path, "wb") as handle:
    remaining = mb * 1024 * 1024
    while remaining:
        chunk = os.urandom(min(8 * 1024 * 1024, remaining))
        handle.write(chunk)
        remaining -= len(chunk)
PY
fi

TOKEN="$(curl -fsS -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" | json_get access_token)"
AUTH="Authorization: Bearer $TOKEN"
ROOT="$(curl -fsS -H "$AUTH" "$BASE/api/root" | json_get id)"
NAME="http-ab-$(date +%s).bin"
echo "== Upload =="
curl -sS -X PUT "$BASE/api/uploads/simple?parent_id=$ROOT&name=$NAME" \
  -H "$AUTH" --data-binary "@$FILE" -o /tmp/ifs-ab-upload.json \
  -w 'http=%{http_code} time=%{time_total}s rate=%{speed_upload}B/s\n'
ENTRY="$(json_get id < /tmp/ifs-ab-upload.json)"
trap 'curl -sS -o /dev/null -X DELETE -H "$AUTH" "$BASE/api/entries/$ENTRY" || true; rm -f "$FILE"' EXIT

echo "== Warm-up =="
curl -sS -H "$AUTH" "$BASE/api/entries/$ENTRY/content" -o /dev/null || true

for PAR in 4 1; do
  echo "== Parallelität $PAR =="
  set_env "$PAR"
  docker compose up -d api >/tmp/ifs-ab-up.log 2>&1
  wait_ready
  for run in 1 2 3 4 5; do
    curl -sS -H "$AUTH" "$BASE/api/entries/$ENTRY/content" -o /dev/null \
      -w "run=$run http=%{http_code} time=%{time_total}s rate=%{speed_download}B/s\n"
  done
done

# Vergleichbarer Ausgangszustand: Default 4.
set_env 4
docker compose up -d api >/tmp/ifs-ab-up.log 2>&1
wait_ready
echo "wieder auf 4 gesetzt"
echo A_B_DONE
