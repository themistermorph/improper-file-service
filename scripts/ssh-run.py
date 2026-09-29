"""Kleiner SSH-Runner für Tests/Deployment.

Zugangsdaten ausschließlich über Umgebungsvariablen:
    IFS_DEPLOY_HOST, IFS_DEPLOY_USER, IFS_DEPLOY_PASSWORD, IFS_DEPLOY_PORT (optional)
    IFS_DEPLOY_SUDO=1  -> Kommando via `sudo -S` als root ausführen

Aufruf:
    python scripts/ssh-run.py "uname -a"          # Kommando als Argument
    python scripts/ssh-run.py @script.sh         # Kommando aus lokaler Datei
    python scripts/ssh-run.py < script.sh        # Kommando von stdin
"""

from __future__ import annotations

import os
import pathlib
import shlex
import sys

from deploy_common import connect


def read_command(argv: list[str]) -> str:
    if len(argv) > 1:
        arg = argv[1]
        if arg.startswith("@"):
            return pathlib.Path(arg[1:]).read_text(encoding="utf-8")
        return arg
    return sys.stdin.read()


def main() -> int:
    # Windows-Konsolen nutzen oft cp1252 und können Unicode-Boxzeichen nicht ausgeben.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    command = read_command(sys.argv).strip()
    if not command:
        print("Kein Kommando übergeben.", file=sys.stderr)
        return 2

    password = os.environ.get("IFS_DEPLOY_PASSWORD") or ""
    use_sudo = os.environ.get("IFS_DEPLOY_SUDO") == "1"

    if use_sudo:
        command = f"sudo -S -p '' bash -c {shlex.quote(command)}"

    client = connect()
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=3600)
        if use_sudo:
            stdin.write(password + "\n")
            stdin.flush()
        stdin.close()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        status = stdout.channel.recv_exit_status()
        if out:
            sys.stdout.write(out)
        if err:
            sys.stderr.write(err)
        return status
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
