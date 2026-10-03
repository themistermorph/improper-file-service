"""ZIP-Download von Ordnern/Dateien (einzeln oder mehrere) über einen kurzlebigen Token.

Große Ordner werden als **Hintergrund-Job** erstellt (`/archive/jobs`), dessen Fortschritt
der Client über `/archive/jobs/{token}` abfragen und danach über `/archive/{token}`
herunterladen kann. Der direkte Weg (Token sofort + Download) bleibt kompatibel.
"""

from __future__ import annotations

import os
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import archive, archive_jobs, audit, authz, namespace
from ..errors import NotFound
from ..models import Entry, User
from ..security import create_archive_token, decode_archive_token
from .deps import client_ip, get_current_user, get_db
from .schemas import ArchiveBulkCreate, ArchiveJobOut, ArchiveJobStatus, ArchiveTokenOut

router = APIRouter(tags=["archive"])

ARCHIVE_TTL_SECONDS = 300
ARCHIVE_JOB_TTL_SECONDS = 3600
CHUNK_SIZE = 1024 * 1024
# Bind-Parameter je IN-Query begrenzen (SQLite alte Builds: max. 999).
IN_CHUNK_SIZE = 500


def _authenticate_archive_token(db: Session, token: str) -> tuple[User, dict]:
    """Prüft einen Archiv-Token vollständig (Signatur, Zweck, Nutzer, token_version).

    Wird von allen Archiv-Endpunkten (Status, Abbruch, Download) genutzt, damit
    ein scoped Token nach Passwortänderung/Deaktivierung sofort ungültig ist.
    """
    payload = decode_archive_token(token)
    if payload is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Ungültiger oder abgelaufener Archiv-Token"
        )
    try:
        user_id = UUID(str(payload["sub"]))
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ungültiger Archiv-Token")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Benutzer unbekannt oder inaktiv")
    if int(payload.get("tv", -1)) != user.token_version:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Archiv-Token ist nicht mehr gültig"
        )
    return user, payload


def _authorize_entries(db: Session, user: User, entries) -> None:
    for entry in entries:
        authz.authorize(db, user, "read", entry)


def _load_entries(db: Session, entry_ids: list[UUID]) -> list[Entry]:
    """Lädt alle Einträge gebündelt; Reihenfolge/Duplikate bleiben wie übergeben.

    Fehlende oder im Papierkorb liegende Einträge führen wie zuvor zu
    ``NotFound`` ("Eintrag nicht gefunden").
    """
    unique_ids = list(dict.fromkeys(entry_ids))
    found: dict[UUID, Entry] = {}
    for start in range(0, len(unique_ids), IN_CHUNK_SIZE):
        chunk = unique_ids[start : start + IN_CHUNK_SIZE]
        for entry in db.execute(select(Entry).where(Entry.id.in_(chunk))).scalars().all():
            found[entry.id] = entry
    entries: list[Entry] = []
    for entry_id in entry_ids:
        entry = found.get(entry_id)
        if entry is None or entry.trashed_at is not None:
            raise NotFound("Eintrag nicht gefunden")
        entries.append(entry)
    return entries


def _archive_name(entries: list[Entry]) -> str:
    return f"{entries[0].name}.zip" if len(entries) == 1 else "auswahl.zip"


def _zip_response(
    path: str,
    filename: str,
    request: Request,
    db: Session,
    user: User,
    entry_count: int,
) -> StreamingResponse:
    size = os.path.getsize(path)
    quoted = quote(filename)
    headers = {
        "Content-Disposition": f"attachment; filename=\"download.zip\"; filename*=UTF-8''{quoted}",
        "Content-Length": str(size),
    }
    audit.record(
        db, "transfer.archive", actor_id=user.id, protocol="http", ip=client_ip(request),
        details={"entries": entry_count, "size": size},
    )

    def iterator():
        try:
            with open(path, "rb") as handle:
                yield from iter(lambda: handle.read(CHUNK_SIZE), b"")
        finally:
            archive.remove_zip(path)

    return StreamingResponse(iterator(), media_type="application/zip", headers=headers)


@router.post("/entries/{entry_id}/archive-token", response_model=ArchiveTokenOut)
def create_token(
    entry_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ArchiveTokenOut:
    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "read", entry)
    token = create_archive_token(
        [str(entry.id)], str(user.id), user.token_version, ARCHIVE_TTL_SECONDS
    )
    return ArchiveTokenOut(
        token=token,
        url=f"/api/archive/{token}",
        expires_in=ARCHIVE_TTL_SECONDS,
        name=f"{entry.name}.zip",
    )


@router.post("/archive/bulk-token", response_model=ArchiveTokenOut)
def create_bulk_token(
    payload: ArchiveBulkCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ArchiveTokenOut:
    if not payload.entry_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keine Einträge ausgewählt")
    entries = _load_entries(db, payload.entry_ids)
    _authorize_entries(db, user, entries)
    token = create_archive_token(
        [str(entry.id) for entry in entries], str(user.id), user.token_version, ARCHIVE_TTL_SECONDS
    )
    return ArchiveTokenOut(
        token=token,
        url=f"/api/archive/{token}",
        expires_in=ARCHIVE_TTL_SECONDS,
        name="auswahl.zip",
    )


@router.post("/archive/jobs", response_model=ArchiveJobOut, status_code=status.HTTP_201_CREATED)
def create_job(
    payload: ArchiveBulkCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ArchiveJobOut:
    """Startet die ZIP-Erstellung im Hintergrund und liefert den Fortschritts-Token."""
    if not payload.entry_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keine Einträge ausgewählt")
    entries = _load_entries(db, payload.entry_ids)
    _authorize_entries(db, user, entries)

    total_files, total_bytes = archive.summarize(db, entries)
    name = _archive_name(entries)
    token = create_archive_token(
        [str(entry.id) for entry in entries],
        str(user.id),
        user.token_version,
        ARCHIVE_JOB_TTL_SECONDS,
    )
    archive_jobs.start(
        token, [str(entry.id) for entry in entries], name, total_files, total_bytes
    )
    audit.record(
        db, "transfer.archive.job", actor_id=user.id, protocol="http", ip=client_ip(request),
        details={"entries": len(entries), "files": total_files, "bytes": total_bytes},
    )
    return ArchiveJobOut(
        token=token,
        name=name,
        url=f"/api/archive/{token}",
        status_url=f"/api/archive/jobs/{token}",
        expires_in=ARCHIVE_JOB_TTL_SECONDS,
        total_files=total_files,
        total_bytes=total_bytes,
    )


@router.get("/archive/jobs/{token}", response_model=ArchiveJobStatus)
def job_status(token: str, db: Session = Depends(get_db)) -> ArchiveJobStatus:
    _authenticate_archive_token(db, token)
    job = archive_jobs.get(token)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Auftrag nicht gefunden oder abgelaufen")
    return ArchiveJobStatus(**job)


@router.delete("/archive/jobs/{token}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_job(token: str, db: Session = Depends(get_db)) -> Response:
    _authenticate_archive_token(db, token)
    archive_jobs.cancel(token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/archive/{token}")
def download_archive(token: str, request: Request, db: Session = Depends(get_db)):
    user, payload = _authenticate_archive_token(db, token)
    raw_ids = payload.get("entries") or ([payload["entry"]] if payload.get("entry") else [])
    try:
        entry_ids = [UUID(str(value)) for value in raw_ids]
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ungültiger Archiv-Token")
    if not entry_ids:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ungültiger Archiv-Token")

    entries = _load_entries(db, entry_ids)
    _authorize_entries(db, user, entries)

    job = archive_jobs.get(token)
    if job is not None:
        if job["status"] == "building":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Archiv wird noch erstellt",
                headers={"Retry-After": "2"},
            )
        if job["status"] == "failed":
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                job.get("error") or "Archiv-Erstellung fehlgeschlagen",
            )
        taken = archive_jobs.take(token)
        if taken is None or not taken.path:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Archivdatei fehlt")
        return _zip_response(taken.path, job["name"], request, db, user, len(entries))

    # Kein Job (Altpfad): synchron erstellen und streamen.
    path = archive.build_zip(db, entries)
    return _zip_response(path, _archive_name(entries), request, db, user, len(entries))
