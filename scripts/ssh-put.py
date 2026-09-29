"""Lädt eine lokale Datei per SFTP auf den Deploy-Host.

Zugangsdaten aus den Umgebungsvariablen wie bei ssh-run.py.

Aufruf:
    python scripts/ssh-put.py <lokale-datei> <zielpfad>
"""

from __future__ import annotations

import sys

from deploy_common import connect


def main() -> int:
    if len(sys.argv) != 3:
        print("Aufruf: ssh-put.py <lokal> <remote>", file=sys.stderr)
        return 2
    local, remote = sys.argv[1], sys.argv[2]

    client = connect()
    try:
        sftp = client.open_sftp()
        sftp.put(local, remote)
        sftp.close()
        print(f"Hochgeladen: {local} -> {remote}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
