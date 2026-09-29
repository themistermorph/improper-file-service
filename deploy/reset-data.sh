#!/usr/bin/env bash
# Voll-Reset der Daten (Datenbank + S3 + Spool) und Neustart des Stacks.
# ACHTUNG: löscht alle Benutzer, Dateien und Einstellungen in den Volumes.
set -uo pipefail
cd /home/ifs/ifs
echo "== Stack stoppen und Volumes entfernen =="
docker compose down -v
echo "== Neu bauen und starten =="
docker compose build
docker compose up -d
echo "== Status =="
docker compose ps
echo RESET_DONE
