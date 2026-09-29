#!/usr/bin/env bash
# Kernverifikation nach dem F1-F4-Patch + Trash-Bulk.
set -uo pipefail
cd /home/ifs/ifs
echo "--- api logs ---"
docker compose logs --tail=8 api 2>&1
echo "--- health ---"
curl -sS -m 5 http://127.0.0.1:8000/healthz; echo
for s in verify-shares verify-share-update verify-folder-share verify-trash-bulk check-security-fixes; do
  echo "## $s"
  bash "deploy/$s.sh" >/tmp/v_$s.log 2>&1
  echo "exit=$?"
  tail -n 6 /tmp/v_$s.log
done
echo VERIFY_ALL3_DONE
