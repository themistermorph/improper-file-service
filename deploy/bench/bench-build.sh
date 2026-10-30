#!/usr/bin/env bash
# Misst die Build-Zeiten nach der Dockerfile-/Compose-Optimierung.
set -uo pipefail
cd /home/ifs/ifs

time_build() {
  local label="$1" log="$2" start end rc
  start=$(date +%s)
  docker compose build >"$log" 2>&1
  rc=$?
  end=$(date +%s)
  echo ">> $label: $((end - start)) s (exit=$rc)"
  grep -E 'pip install|COPY src|COPY pyproject|CACHED|exporting|unpacking|DONE [0-9]' "$log" \
    | grep -vE 'transferring|load ' | tail -14
  echo
}

echo "== 1) Kalt-Build (Abhängigkeits-Layer wird neu gebaut) =="
time_build "cold" /tmp/bench-cold.log

echo "== 2) Reine Code-Änderung (nur src) =="
echo "# bench $(date +%s)" > src/ifs/_bench_marker.py
time_build "code-change" /tmp/bench-code.log
rm -f src/ifs/_bench_marker.py

echo "== 3) Wiederherstellung (Marker entfernt) =="
time_build "restore" /tmp/bench-restore.log

echo "== Images =="
docker images --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep -E 'ifs' || true
echo BENCH_DONE
