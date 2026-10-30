#!/usr/bin/env bash
# Prüft, ob die Kleindatei-Optimierungen im laufenden FTP-Container aktiv sind.
set -uo pipefail
cd /home/ifs/ifs

echo "== load =="
uptime

docker compose exec -T ftp python - <<'PY'
import inspect

from ifs.core import content, namespace
from ifs.ftp import fs, server

print("upload_file nutzt put_object:", "put_object" in inspect.getsource(content.upload_file))
print("_open_writer nutzt read_scope:", "read_scope" in inspect.getsource(fs.IFSFilesystem._open_writer))
print("has_perm nutzt read_scope:", "read_scope" in inspect.getsource(server.IFSAuthorizer.has_perm))
print(
    "ensure_writable liefert Eintrag:",
    "return _existing_writable" in inspect.getsource(namespace.ensure_writable),
)
print(
    "finalize nutzt ensure_writable-Rückgabe:",
    "existing = namespace.ensure_writable" in inspect.getsource(fs.IFSFilesystem.finalize_received),
)
PY
