#!/usr/bin/env bash
# Lädt das über den Share-Link bereitgestellte Bild auf dem Testhost.
set -uo pipefail
URL="http://127.0.0.1:8000/api/shares/-7NQE4KiognkXOhBn0ht94p8wVUr7VSx/content/IMG_5059.jpeg"
OUT="/tmp/ifs-share-image"

http=$(curl -sS -m30 -w '%{http_code} %{content_type}' -o "$OUT" "$URL")
status=$?
echo "curl_status=$status http=$http"
ls -l "$OUT" || true
python3 - "$OUT" <<'PY'
import os
import sys

path = sys.argv[1]
if not os.path.exists(path):
    print("MISSING")
    raise SystemExit(1)
with open(path, "rb") as handle:
    head = handle.read(16)
print("size=", os.path.getsize(path), "head=", head)
PY
