#!/usr/bin/env bash
# Kurzer Versions-/Health-Check nach dem Ausrollen.
set -uo pipefail
echo "version: $(curl -sS -m5 http://127.0.0.1:8000/version)"
echo "healthz: $(curl -sS -m5 http://127.0.0.1:8000/healthz)"
echo CHECK_VERSION_DONE
