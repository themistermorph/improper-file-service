"""Bootstrap: Host-Schlüssel des Deploy-Hosts einmalig holen und pinnen.

Sicherheitshinweis: Der erste Kontakt ist nicht vertrauenswürdig. Die ausgegebenen
Fingerprints müssen **out-of-band** bestätigt werden. Danach prüfen alle Deploy-Skripte
den Host-Key gegen die gepinnte Datei (`IFS_DEPLOY_KNOWN_HOSTS`).

Aufruf:
    python scripts/pin_hostkey.py [zieldatei]
"""

from __future__ import annotations

import base64
import hashlib
import os
import pathlib
import sys

import paramiko


def main() -> int:
    host = os.environ["IFS_DEPLOY_HOST"]
    port = int(os.environ.get("IFS_DEPLOY_PORT", "22"))
    target = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "deploy/known_hosts")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        host,
        port=port,
        username=os.environ["IFS_DEPLOY_USER"],
        password=os.environ.get("IFS_DEPLOY_PASSWORD") or None,
        key_filename=os.environ.get("IFS_DEPLOY_KEY") or None,
        timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    try:
        keys = client.get_host_keys()
    finally:
        client.close()

    target.parent.mkdir(parents=True, exist_ok=True)
    keys.save(str(target))
    print(f"Host-Keys gespeichert: {target}")
    for hostname, entry in keys.items():
        for key_type, key in entry.items():
            digest = hashlib.sha256(key.asbytes()).digest()
            fingerprint = base64.b64encode(digest).decode().rstrip("=")
            print(f"  {key_type:10s} {hostname}  SHA256:{fingerprint}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
