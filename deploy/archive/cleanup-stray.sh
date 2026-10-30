#!/usr/bin/env bash
# Entfernt versehentlich mit Windows-Backslash benannte Dateien im Projektwurzelverzeichnis.
set -uo pipefail
python3 - <<'PY'
import os

root = "/home/ifs/ifs"
removed = []
for name in os.listdir(root):
    path = os.path.join(root, name)
    if os.path.isfile(path) and "\\" in name:
        os.remove(path)
        removed.append(name)
print("entfernt:", len(removed))
for name in sorted(removed)[:5]:
    print("  ", name)
PY
