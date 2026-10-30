"""Lädt eine Datei vom Deploy-Host (Gegenstück zu ssh-push.py).

Zugangsdaten aus den Umgebungsvariablen wie bei ssh-run.py:
    IFS_DEPLOY_HOST, IFS_DEPLOY_USER, IFS_DEPLOY_PASSWORD, IFS_DEPLOY_PORT (optional)
    IFS_DEPLOY_KNOWN_HOSTS

Aufruf:
    python scripts/ssh-pull.py /remote/path ./local/path
"""

from __future__ import annotations

import sys

from deploy_common import connect


def main() -> int:
    if len(sys.argv) != 3:
        print("Aufruf: ssh-pull.py <remote> <local>", file=sys.stderr)
        return 2
    remote, local = sys.argv[1], sys.argv[2]
    client = connect()
    try:
        sftp = client.open_sftp()
        try:
            sftp.get(remote, local)
        finally:
            sftp.close()
        print(f"{remote} -> {local}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
