"""Vergleicht den lokalen Projektstand mit dem Testsystem (per SSH).

Zugangsdaten aus den Umgebungsvariablen wie bei ssh-run.py.

Aufruf:
    python scripts/check-sync.py
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

from deploy_common import connect

ROOT = pathlib.Path(__file__).resolve().parent.parent
REMOTE = "/home/ifs/ifs"
EXCLUDE_DIRS = {".venv", ".git", ".github", ".pytest_cache", ".ruff_cache", "__pycache__", "certs", "deploy"}
INCLUDE_SUFFIXES = {".py", ".md", ".html", ".toml", ".yml", ".yaml", ".ini", ".mako", ".sql"}
INCLUDE_NAMES = {".env.example"}
REMOTE_FIND = (
    "cd /home/ifs/ifs && find . -type f "
    "\\( -name '*.py' -o -name '*.md' -o -name '*.html' -o -name '*.toml' "
    "-o -name '*.yml' -o -name '*.ini' -o -name '*.mako' -o -name '*.sql' "
    "-o -name '.env.example' \\) "
    "-not -path './.venv/*' -not -path './.pytest_cache/*' -not -path './.ruff_cache/*' "
    "-not -path './deploy/*' | sort | xargs -r sha256sum"
)


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_manifest() -> dict[str, str]:
    manifest: dict[str, str] = {}
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix not in INCLUDE_SUFFIXES and path.name not in INCLUDE_NAMES:
            continue
        manifest["./" + str(path.relative_to(ROOT)).replace("\\", "/")] = digest(path)
    return manifest


def remote_manifest() -> dict[str, str]:
    client = connect()
    try:
        _, stdout, _ = client.exec_command(REMOTE_FIND, timeout=120)
        output = stdout.read().decode("utf-8", "replace")
    finally:
        client.close()
    manifest: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            manifest[parts[1].strip()] = parts[0]
    return manifest


def main() -> int:
    local = local_manifest()
    remote = remote_manifest()

    only_local = sorted(set(local) - set(remote))
    only_remote = sorted(set(remote) - set(local))
    differing = sorted(p for p in set(local) & set(remote) if local[p] != remote[p])

    print(f"Lokal: {len(local)} Dateien, Server: {len(remote)} Dateien")
    if only_local:
        print("\nNur lokal:")
        for path in only_local:
            print("  ", path)
    if only_remote:
        print("\nNur auf dem Server:")
        for path in only_remote:
            print("  ", path)
    if differing:
        print("\nUnterschiedlich:")
        for path in differing:
            print("  ", path)
    if not (only_local or only_remote or differing):
        print("\nAlles synchron.")
    return 1 if (only_local or only_remote or differing) else 0


if __name__ == "__main__":
    sys.exit(main())
