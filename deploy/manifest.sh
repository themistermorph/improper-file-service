#!/usr/bin/env bash
# Gibt Hashes der relevanten Projektdateien aus (Vergleich Server <-> lokal).
cd /home/ifs/ifs || exit 1
find . -type f \
  \( -name '*.py' -o -name '*.md' -o -name '*.html' -o -name '*.toml' \
     -o -name '*.yml' -o -name '*.ini' -o -name '*.mako' -o -name '.env.example' \) \
  -not -path './.venv/*' -not -path './.pytest_cache/*' -not -path './.ruff_cache/*' \
  | sort | xargs -r sha256sum
