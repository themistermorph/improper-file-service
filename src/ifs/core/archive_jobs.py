"""Hintergrund-Erstellung von ZIP-Archiven mit Fortschritt.

Die Jobs liegen prozesslokal (die API läuft mit einem Worker). Der Fertigstellungspfad
wird nach dem Download bzw. nach Ablauf automatisch gelöscht.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select

from ..db import session_scope
from ..models import Entry
from . import archive

JOB_TTL_SECONDS = 1800
MAX_JOBS = 32
# Fortschritt höchstens so oft unter dem Lock aktualisieren (der Endstand geht
# immer durch). Polling-Clients sehen dadurch dieselben Endwerte, aber deutlich
# weniger Lock-/Dict-Schreibvorgänge bei sehr vielen kleinen Dateien.
PROGRESS_UPDATE_INTERVAL = 0.1
# Bind-Parameter je IN-Query begrenzen (SQLite alte Builds: max. 999).
IN_CHUNK_SIZE = 500


@dataclass
class ArchiveJob:
    token: str
    name: str
    total_files: int
    total_bytes: int
    files_done: int = 0
    bytes_done: int = 0
    state: str = "building"  # building | ready | failed
    path: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "name": self.name,
            "status": self.state,
            "files_done": self.files_done,
            "total_files": self.total_files,
            "bytes_done": self.bytes_done,
            "total_bytes": self.total_bytes,
            "error": self.error,
        }


_jobs: dict[str, ArchiveJob] = {}
_lock = threading.Lock()


def _drop_locked(token: str) -> ArchiveJob | None:
    job = _jobs.pop(token, None)
    if job and job.path:
        archive.remove_zip(job.path)
    return job


def _sweep() -> None:
    now = time.monotonic()
    with _lock:
        for token in [t for t, job in _jobs.items() if now - job.created_at > JOB_TTL_SECONDS]:
            _drop_locked(token)
        overflow = len(_jobs) - MAX_JOBS
        if overflow > 0:
            oldest = sorted(_jobs, key=lambda key: _jobs[key].created_at)[:overflow]
            for token in oldest:
                _drop_locked(token)


def start(
    token: str,
    entry_ids: list[str],
    name: str,
    total_files: int,
    total_bytes: int,
) -> None:
    job = ArchiveJob(
        token=token, name=name, total_files=total_files, total_bytes=total_bytes
    )
    with _lock:
        _jobs[token] = job
    _sweep()
    thread = threading.Thread(
        target=_run, args=(token, list(entry_ids)), daemon=True, name=f"ifs-zip-{token[:8]}"
    )
    thread.start()


def _run(token: str, entry_ids: list[str]) -> None:
    path: str | None = None
    latest_bytes = 0
    try:
        with session_scope() as db:
            ids = [UUID(str(raw)) for raw in entry_ids]
            found: dict[UUID, Entry] = {}
            for start in range(0, len(ids), IN_CHUNK_SIZE):
                chunk = ids[start : start + IN_CHUNK_SIZE]
                for entry in db.execute(
                    select(Entry).where(Entry.id.in_(chunk))
                ).scalars().all():
                    found[entry.id] = entry
            # Reihenfolge und Duplikate der Anfrage bleiben erhalten; fehlende
            # Einträge werden wie zuvor übersprungen.
            entries = [found[entry_id] for entry_id in ids if entry_id in found]

            with _lock:
                current = _jobs.get(token)
                total_files = current.total_files if current is not None else 0

            last_update = 0.0

            def progress(files_done: int, bytes_done: int) -> None:
                nonlocal last_update, latest_bytes
                latest_bytes = bytes_done
                now = time.monotonic()
                if files_done < total_files and now - last_update < PROGRESS_UPDATE_INTERVAL:
                    return
                last_update = now
                with _lock:
                    job = _jobs.get(token)
                    if job is not None:
                        job.files_done = files_done
                        job.bytes_done = bytes_done

            path = archive.build_zip(db, entries, progress)
        with _lock:
            job = _jobs.get(token)
            if job is None:
                # Abgebrochen (cancel/TTL/Sweep): Die Datei darf nicht im Spool
                # zurückbleiben, der nächste Sweep findet sie nicht mehr.
                archive.remove_zip(path)
            else:
                job.path = path
                job.state = "ready"
                job.files_done = job.total_files
                # Letzter gemeldeter Stand (entspricht dem früheren Endwert).
                job.bytes_done = latest_bytes
    except Exception as exc:  # Fehler an den Client melden
        if path is not None:
            # Nach dem Build aufgetretener Fehler: keine Leiche hinterlassen.
            archive.remove_zip(path)
        with _lock:
            job = _jobs.get(token)
            if job is not None:
                job.state = "failed"
                job.error = str(exc)


def get(token: str) -> dict | None:
    with _lock:
        job = _jobs.get(token)
        return job.to_dict() if job is not None else None


def take(token: str) -> ArchiveJob | None:
    """Entfernt den Job aus der Registry und liefert ihn (Pfad bleibt erhalten)."""
    with _lock:
        return _jobs.pop(token, None)


def cancel(token: str) -> bool:
    with _lock:
        return _drop_locked(token) is not None


def reset() -> None:
    """Für Tests: alle Jobs und Dateien verwerfen."""
    with _lock:
        for token in list(_jobs):
            _drop_locked(token)
