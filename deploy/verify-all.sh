#!/usr/bin/env bash
# Führt die wichtigsten Verifikationsskripte nacheinander aus.
set -uo pipefail
cd /home/ifs/ifs
for s in verify verify-report verify-hardening verify-shares verify-share-update verify-archive-job verify-folder-share; do
  echo "## $s"
  bash "deploy/$s.sh"
  echo "exit=$?"
done
echo VERIFY_ALL_DONE
