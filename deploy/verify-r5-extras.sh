#!/usr/bin/env bash
# Restliche Checks nach Review 5.
set -uo pipefail
cd /home/ifs/ifs
for s in verify-review4 check-security-fixes check-security-fixes2 verify-trash-bulk verify-bulk-admin; do
  echo "## $s"
  bash "deploy/$s.sh" >/tmp/r5_$s.log 2>&1
  echo "exit=$?"
  tail -n 4 /tmp/r5_$s.log
done
echo "## ftp-check"
bash deploy/ftp-check.sh >/tmp/r5_ftp.log 2>&1
echo "exit=$?"
tail -n 4 /tmp/r5_ftp.log
echo R5_EXTRAS_DONE
