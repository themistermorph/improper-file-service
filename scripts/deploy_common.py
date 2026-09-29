"""Gemeinsame SSH-Verbindung für die Deploy-Skripte.

Sicherheit:
- Host-Schlüssel werden **geprüft** (RejectPolicy). Nur mit explizitem Opt-in
  (`IFS_DEPLOY_HOSTKEY_POLICY=auto`) wird ein unbekannter Schlüssel akzeptiert.
- Empfohlen ist **Key-Authentifizierung** über `IFS_DEPLOY_KEY`; Passwort nur,
  wenn kein Key gesetzt ist (`IFS_DEPLOY_PASSWORD`).
- Zugangsdaten kommen ausschließlich aus Umgebungsvariablen, nie aus Dateien/Argumenten.

Umgebungsvariablen: IFS_DEPLOY_HOST, IFS_DEPLOY_USER, IFS_DEPLOY_PORT,
IFS_DEPLOY_KEY (Pfad), IFS_DEPLOY_PASSWORD, IFS_DEPLOY_KNOWN_HOSTS (Pfad),
IFS_DEPLOY_HOSTKEY_POLICY (reject|auto).
"""

from __future__ import annotations

import os

import paramiko


def connect() -> paramiko.SSHClient:
    host = os.environ["IFS_DEPLOY_HOST"]
    user = os.environ["IFS_DEPLOY_USER"]
    port = int(os.environ.get("IFS_DEPLOY_PORT", "22"))
    key_filename = os.environ.get("IFS_DEPLOY_KEY") or None
    password = os.environ.get("IFS_DEPLOY_PASSWORD") or None

    client = paramiko.SSHClient()
    known_hosts = os.environ.get("IFS_DEPLOY_KNOWN_HOSTS")
    if known_hosts:
        client.load_host_keys(known_hosts)
    else:
        try:
            client.load_system_host_keys()
        except Exception:
            # Fehlende Host-Key-Datei ist unkritisch; RejectPolicy greift trotzdem.
            pass

    policy = os.environ.get("IFS_DEPLOY_HOSTKEY_POLICY", "reject").strip().lower()
    if policy == "auto":
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

    client.connect(
        host,
        port=port,
        username=user,
        password=password,
        key_filename=key_filename,
        timeout=30,
        banner_timeout=30,
        auth_timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    return client
