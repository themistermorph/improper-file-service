"""ZIP-Archiv eines Eintrags (Ordner inklusive Teilbaum) aus dem Objektspeicher.

Das Archiv wird in eine temporäre Datei im Spool-Verzeichnis geschrieben und danach
gestreamt – so bleibt der Speicherbedarf konstant (kein vollständiges Laden in den RAM).
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Blob, Entry, EntryType, Version
from . import content, namespace


def _date_time(entry: Entry) -> tuple[int, int, int, int, int, int]:
    value = entry.updated_at
    if value is None:
        return (1980, 1, 1, 0, 0, 0)
    return (max(value.year, 1980), value.month, value.day, value.hour, value.minute, value.second)


def _add_entry(
    db: Session,
    zf: zipfile.ZipFile,
    entry: Entry,
    prefix: str,
    on_file: Callable[[int], None],
) -> None:
    if entry.type == EntryType.folder:
        zf.writestr(prefix + "/", b"")
        for child in namespace.list_children(db, entry):
            _add_entry(db, zf, child, prefix + "/" + child.name, on_file)
        return

    if entry.current_version_id is None:
        on_file(0)
        return
    # Version und Blob in einer Abfrage (statt zwei Einzel-Lookups je Datei).
    row = db.execute(
        select(Version, Blob)
        .join(Blob, Blob.id == Version.blob_id)
        .where(Version.id == entry.current_version_id)
    ).first()
    if row is None:
        on_file(0)
        return
    _version, blob = row

    info = zipfile.ZipInfo(prefix)
    info.date_time = _date_time(entry)
    info.compress_type = zipfile.ZIP_DEFLATED
    response = content.get_object(blob.storage_key)
    with zf.open(info, "w") as dest:
        for chunk in content.stream_out(response["Body"]):
            dest.write(chunk)
    on_file(int(blob.size or 0))


def _unique_prefix(used: set[str], name: str) -> str:
    """Sucht einen freien Namen (``name``, ``name (2)``, ``name (3)`` …) und merkt ihn vor.

    Dadurch kollidiert auch eine Eingabe wie ``a (2)`` nicht mit einem zuvor für
    ein anderes ``a`` vergebenen Ausweichnamen.
    """
    prefix = name
    suffix = 2
    while prefix in used:
        prefix = f"{name} ({suffix})"
        suffix += 1
    used.add(prefix)
    return prefix


def summarize(db: Session, entries: list[Entry]) -> tuple[int, int]:
    """Zählt Dateien und deren Gesamtgröße im Teilbaum (für die Fortschrittsanzeige)."""
    files = 0
    total = 0
    stack = list(entries)
    while stack:
        entry = stack.pop()
        if entry.type == EntryType.folder:
            stack.extend(namespace.list_children(db, entry))
        else:
            files += 1
            total += int(entry.size or 0)
    return files, total


def build_zip(
    db: Session,
    entries: list[Entry],
    progress: Callable[[int, int], None] | None = None,
) -> str:
    """Erzeugt ein ZIP aus einem oder mehreren Einträgen und liefert den Dateipfad.

    ``progress(files_done, bytes_done)`` wird nach jeder verarbeiteten Datei aufgerufen.
    """
    spool = get_settings().upload_spool_dir
    os.makedirs(spool, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="ifs-zip-", suffix=".zip", dir=spool)
    os.close(fd)

    counter = {"files": 0, "bytes": 0}

    def on_file(size: int) -> None:
        counter["files"] += 1
        counter["bytes"] += size
        if progress is not None:
            progress(counter["files"], counter["bytes"])

    used: set[str] = set()
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for entry in entries:
                prefix = _unique_prefix(used, entry.name)
                _add_entry(db, zf, entry, prefix, on_file)
    except Exception:
        remove_zip(path)
        raise
    return path


def remove_zip(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
