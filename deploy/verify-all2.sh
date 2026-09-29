#!/usr/bin/env bash
# Zweite Runde: restliche Feature-Verifikationen.
set -uo pipefail
cd /home/ifs/ifs
for s in verify-monitor verify-users verify-roles verify-trash verify-share-trash verify-versions verify-preview verify-bulk verify-archive verify-ratelimit; do
  echo "## $s"
  bash "deploy/$s.sh"
  echo "exit=$?"
done
echo VERIFY_ALL2_DONE
