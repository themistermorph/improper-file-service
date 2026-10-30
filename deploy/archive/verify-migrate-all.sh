#!/usr/bin/env bash
# Regressionssuiten nach der Migration (sequenziell).
set -uo pipefail
cd /home/ifs/ifs
for s in verify-all verify-all2 verify-r5-extras; do
  echo "## $s"
  bash "deploy/$s.sh" >/tmp/mig_$s.log 2>&1
  echo "exit=$?"
  grep -E '^## |^exit=|_DONE|FAIL|FEHLER' /tmp/mig_$s.log | tail -50
done
echo VERIFY_MIGRATE_ALL_DONE
