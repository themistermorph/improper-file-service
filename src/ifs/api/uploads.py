"""Upload-Endpunkte: einfacher Upload (PUT) und resumable Sessions (tus-ähnlich)."""

from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core import audit, authz, content, namespace, uploads
from ..errors import QuotaExceeded
from ..models import User
from .deps import client_ip, get_current_user, get_db
from .schemas import UploadCreate, UploadOut

router = APIRouter(tags=["uploads"])


def _check_owner(session, user: User) -> None:
    # Upload-Sessions sind strikt nutzergebunden; auch Systemadmins haben
    # keinen Dateizugriff. `complete`/`PATCH` prüfen zusätzlich `write` am Ziel.
    if session.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Upload-Session gehört einem anderen Nutzer")


@router.post("/uploads", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
def create_upload(
    payload: UploadCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UploadOut:
    parent = namespace.get_entry(db, payload.parent_id)
    authz.authorize(db, user, "write", parent)
    limit = get_settings().max_upload_size
    if limit and payload.size and payload.size > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Datei zu groß")
    session = uploads.create_session(
        db,
        parent,
        payload.name,
        user.id,
        size_expected=payload.size,
        sha256_expected=payload.sha256,
        protocol="http",
    )
    audit.record(
        db, "upload.create", actor_id=user.id, target_entry=parent.id,
        protocol="http", ip=client_ip(request), details={"upload_id": str(session.id)},
    )
    return UploadOut(id=session.id, offset=session.offset, status=session.status.value)


@router.head("/uploads/{upload_id}")
def upload_status(
    upload_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    session = uploads.get_session(db, upload_id)
    _check_owner(session, user)
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={
            "Upload-Offset": str(session.offset),
            "Upload-Length": str(session.size_expected or session.offset),
        },
    )


@router.patch("/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def patch_upload(
    upload_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    upload_offset: int | None = None,
) -> Response:
    session = uploads.get_session(db, upload_id, for_update=True)
    _check_owner(session, user)
    # Rechte am Zielordner erneut prüfen (zwischen Init und Transfer entzogen?)
    authz.authorize(db, user, "write", namespace.get_entry(db, session.parent_id))

    offset = upload_offset
    if offset is None:
        header = request.headers.get("upload-offset")
        if header is None:
            offset = session.offset
        else:
            try:
                offset = int(header)
            except ValueError:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültiger Upload-Offset")

    path = uploads.begin_append(db, session, offset)
    written = 0
    limit = get_settings().max_upload_size
    exceeded = False
    with open(path, "ab") as handle:
        async for chunk in request.stream():
            if not chunk:
                continue
            if limit and session.offset + written + len(chunk) > limit:
                # Nicht mehr schreiben; Abbruch passiert nach dem Schließen.
                exceeded = True
                break
            handle.write(chunk)
            written += len(chunk)

    if exceeded:
        # Abbruch explizit committen: get_db rollt bei der HTTPException zurück,
        # sonst bliebe die Session "pending" und der Spool wäre bereits gelöscht.
        uploads.abort(db, session)
        db.commit()
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Datei zu groß")

    try:
        uploads.finish_append(db, session, written)
    except QuotaExceeded:
        # finish_append hat die Session bereits abgebrochen; der Commit sichert
        # den Abbruch gegen den Rollback in get_db. 413 kommt vom IFSError-Handler.
        db.commit()
        raise

    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={"Upload-Offset": str(session.offset)},
    )


@router.post("/uploads/{upload_id}/complete", status_code=status.HTTP_201_CREATED)
def complete_upload(
    upload_id: UUID,
    request: Request,
    mime: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    session = uploads.get_session(db, upload_id, for_update=True)
    _check_owner(session, user)
    authz.authorize(db, user, "write", namespace.get_entry(db, session.parent_id))
    entry = uploads.complete(db, user, session, mime)
    audit.record(
        db, "upload.complete", actor_id=user.id, target_entry=entry.id,
        protocol="http", ip=client_ip(request), details={"upload_id": str(upload_id)},
    )
    return {"id": str(entry.id), "name": entry.name, "size": entry.size, "sha256": entry.sha256}


@router.delete("/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def abort_upload(
    upload_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    session = uploads.get_session(db, upload_id)
    _check_owner(session, user)
    uploads.abort(db, session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/uploads/simple", status_code=status.HTTP_201_CREATED)
async def simple_upload(
    request: Request,
    parent_id: UUID | None = None,
    name: str = "",
    mime: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Upload in einem Rutsch (Body = Dateiinhalt)."""
    parent = (
        namespace.get_entry(db, parent_id)
        if parent_id
        else namespace.ensure_root(db, user.id)
    )
    if parent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zielverzeichnis nicht gefunden")
    authz.authorize(db, user, "write", parent)
    namespace.validate_name(name)

    limit = get_settings().max_upload_size
    declared = request.headers.get("content-length")
    if limit and declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Datei zu groß")

    spool = content.new_spool_path()
    size = 0
    digest = hashlib.sha256()
    try:
        with open(spool, "wb") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                if limit and size + len(chunk) > limit:
                    raise HTTPException(
                        status.HTTP_413_CONTENT_TOO_LARGE, "Datei zu groß"
                    )
                handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        namespace.ensure_writable(db, parent, name, user.id, size)
        # Der SHA-256 entsteht beim Schreiben des Streams; store_blob muss die
        # Datei dadurch nicht erneut vollständig lesen (SpoolRef trägt ihn mit).
        blob = content.store_blob(
            db, content.SpoolRef(spool, digest.hexdigest(), size), mime
        )
    finally:
        content.remove_spool(spool)

    entry = namespace.apply_write(db, parent, name, user.id, blob, mime)
    audit.record(
        db, "upload.simple", actor_id=user.id, target_entry=entry.id,
        protocol="http", ip=client_ip(request), details={"size": blob.size},
    )
    return {"id": str(entry.id), "name": entry.name, "size": entry.size, "sha256": entry.sha256}
