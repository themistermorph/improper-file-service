"""Prozess-Logs: gemeinsames Logverzeichnis und Tail-Lesen für das Admin-Panel.

Die Container `api`, `ftp` und `worker` schreiben jeweils in eine eigene Datei
unter ``/var/log/ifs``. Das Verzeichnis wird in ``docker-compose.yml`` als
gemeinsames Volume eingebunden; die API liest daraus für das Log-Panel.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

LOG_DIR = Path("/var/log/ifs")
SERVICES = ("api", "ftp", "worker")
MAX_TAIL_BYTES = 512 * 1024


def log_path(service: str) -> Path:
    return LOG_DIR / f"{service}.log"


def service_info(service: str) -> dict:
    path = log_path(service)
    try:
        stat = path.stat()
    except OSError:
        return {
            "service": service,
            "available": False,
            "size": 0,
            "updated_at": None,
        }
    return {
        "service": service,
        "available": path.is_file(),
        "size": stat.st_size,
        "updated_at": datetime.fromtimestamp(stat.st_mtime, UTC),
    }


def read_tail(service: str, lines: int) -> dict:
    """Liest die letzten ``lines`` Zeilen, ohne die ganze Datei zu laden."""
    path = log_path(service)
    try:
        stat = path.stat()
    except OSError:
        return {
            "service": service,
            "available": False,
            "lines": [],
            "truncated": False,
            "size": 0,
            "updated_at": None,
        }

    size = stat.st_size
    start = max(0, size - MAX_TAIL_BYTES)
    with path.open("rb") as handle:
        handle.seek(start)
        data = handle.read()

    truncated = start > 0
    if truncated and b"\n" in data:
        # Erste (möglicherweise angeschnittene) Zeile verwerfen.
        data = data.split(b"\n", 1)[1]

    text = data.decode("utf-8", "replace")
    all_lines = text.splitlines()
    if len(all_lines) > lines:
        all_lines = all_lines[-lines:]

    return {
        "service": service,
        "available": True,
        "lines": all_lines,
        "truncated": truncated,
        "size": size,
        "updated_at": datetime.fromtimestamp(stat.st_mtime, UTC),
    }
