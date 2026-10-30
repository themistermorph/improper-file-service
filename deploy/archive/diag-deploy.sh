#!/usr/bin/env bash
# Diagnose: warum ist das Ausrollen langsam?
set -uo pipefail
cd /home/ifs/ifs

echo "== compose / buildx =="
docker compose version
if docker buildx version >/dev/null 2>&1; then docker buildx version; else echo "buildx FEHLT (klassischer Builder)"; fi

echo "== docker server =="
docker version --format '{{.Server.Version}}'

echo "== Dockerfile =="
cat Dockerfile

echo "== Ressourcen =="
echo -n "nproc: "; nproc
free -h | head -2
df -h / | tail -1

echo "== Images =="
docker images --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep -E 'ifs|seaweed|caddy|postgres' || true

echo "== pip-Downloads je Build-Log (Hinweis auf Reinstallation) =="
for f in /tmp/ifs-*.log; do
  [ -f "$f" ] || continue
  n=$(grep -c 'Downloading' "$f" 2>/dev/null || echo 0)
  echo "$n  $f"
done

echo "== Build-Historie (letzte Layer-Kommentare) =="
docker history ifs-api:latest --no-trunc --format '{{.Size}}  {{.CreatedSince}}  {{.CreatedBy}}' 2>/dev/null | head -8 || true

echo DIAG_DEPLOY_DONE
