"""Thread-lokaler Kontext für den aktuellen FTP-Schreibbefehl.

``has_perm`` löst für STOR/APPE Parent und Ziel in einer Read-Only-Session auf
und autorisiert beide. ``_open_writer`` übernimmt das Ergebnis ohne eine zweite
DB-Session. Der Kontext gilt nur für den aktuellen Befehl im selben Thread und
wird von ``IFSFTPHandler.pre_process_command`` verwaltet.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from uuid import UUID


@dataclass(slots=True)
class WriteTarget:
    """Vorab aufgelöstes, autorisiertes Ziel eines STOR/APPE."""

    path: str
    user_id: UUID
    parent_id: UUID
    parent_path: str
    name: str
    existing_id: UUID | None = None
    existing_is_folder: bool = False


ctx = threading.local()


def clear() -> None:
    ctx.target = None
    ctx.cmd = None
    ctx.fs_path = None


def take(path: str) -> WriteTarget | None:
    """Liefert den Kontext genau für ``path`` und konsumiert ihn."""
    target = getattr(ctx, "target", None)
    ctx.target = None
    if target is None or target.path != path:
        return None
    return target
