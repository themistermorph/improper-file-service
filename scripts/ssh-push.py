"""Überträgt mehrere Dateien (projektrelativ) auf den Deploy-Host.

Zugangsdaten aus den Umgebungsvariablen wie bei ssh-run.py.

Aufruf:
    python scripts/ssh-push.py README.md docs/api-referenz.md ...
"""

from __future__ import annotations

import os
import pathlib
import sys

from deploy_common import connect

ROOT = pathlib.Path(__file__).resolve().parent.parent
REMOTE = "/home/ifs/ifs"


def main() -> int:
    if len(sys.argv) < 2:
        print("Aufruf: ssh-push.py <datei> [<datei> ...]", file=sys.stderr)
        return 2

    client = connect()
    try:
        sftp = client.open_sftp()
        for relative in sys.argv[1:]:
            # Windows-Pfade (Backslash) auf POSIX-Separatoren normalisieren.
            relative = relative.replace("\\", "/")
            if relative.startswith("./"):
                relative = relative[2:]
            local = ROOT / relative
            remote = f"{REMOTE}/{relative}"
            remote_dir = os.path.dirname(remote)
            # Zielverzeichnis sicherstellen
            parts = remote_dir.split("/")
            current = ""
            for part in parts:
                if part in ("", "/"):
                    continue
                current += "/" + part
                try:
                    sftp.stat(current)
                except OSError:
                    sftp.mkdir(current)
            sftp.put(str(local), remote)
            print(f"-> {relative}")
        sftp.close()
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
