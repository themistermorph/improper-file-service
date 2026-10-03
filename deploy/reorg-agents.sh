#!/usr/bin/env bash
# Bündelt die (nicht synchronisierten) Agent-Dokumente in for-agents/ – analog
# zum lokalen Repo, damit der Sync-Abgleich sauber bleibt.
set -uo pipefail
cd /home/ifs/ifs
mkdir -p for-agents
for f in AGENTEN-UEBERSICHT.md UEBERGABE-*.md agent-guidlines.md; do
  if [ -e "$f" ]; then
    mv -v "$f" for-agents/
  fi
done
echo "== for-agents =="
ls -1 for-agents 2>/dev/null || true
echo REORG_AGENTS_DONE
