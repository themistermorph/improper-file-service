#!/usr/bin/env bash
# Prüft, ob die Papierkorb-Checkboxen im Image und in der ausgelieferten Seite sind.
set -uo pipefail
cd /home/ifs/ifs
echo "im Image:     $(docker compose exec -T api grep -c trash-select-all /app/src/ifs/web/index.html 2>/dev/null || echo 0)"
echo "bulkbar Image: $(docker compose exec -T api grep -c trash-bulkbar /app/src/ifs/web/index.html 2>/dev/null || echo 0)"
echo "ausgeliefert:  $(curl -s http://127.0.0.1:8000/ | grep -c trash-select-all)"
echo "bulkbar Seite: $(curl -s http://127.0.0.1:8000/ | grep -c trash-bulkbar)"
