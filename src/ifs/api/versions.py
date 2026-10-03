"""Versionshistorie und Rollback für Dateien."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import audit, authz, namespace
from ..errors import NotFound
from ..models import Blob, EntryType, User, Version
from .deps import client_ip, get_current_user, get_db
from .entries import stream_blob
from .schemas import VersionOut

router = APIRouter(tags=["versions"])


def _to_out(entry, version: Version) -> VersionOut:
    return VersionOut(
        id=version.id,
        seq=version.seq,
        size=version.size,
        mime=version.mime,
        sha256=version.sha256,
        created_by=version.created_by,
        created_at=version.created_at,
        is_current=version.id == entry.current_version_id,
    )


@router.get("/entries/{entry_id}/versions", response_model=list[VersionOut])
def list_versions(
    entry_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[VersionOut]:
    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "read", entry)
    if entry.type != EntryType.file:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur Dateien haben Versionen")
    return [_to_out(entry, version) for version in namespace.list_versions(db, entry)]


@router.post(
    "/entries/{entry_id}/versions/{version_id}/restore", response_model=VersionOut
)
def restore_version(
    entry_id: UUID,
    version_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> VersionOut:
    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "write", entry)
    version = namespace.get_version(db, entry, version_id)
    restored = namespace.restore_version(db, entry, version, user.id)
    audit.record(
        db, "entry.version_restore", actor_id=user.id, target_entry=entry.id,
        protocol="http", ip=client_ip(request), details={"from_seq": version.seq},
    )
    return _to_out(entry, restored)


@router.get("/entries/{entry_id}/versions/{version_id}/content")
def version_content(
    entry_id: UUID,
    version_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "read", entry)
    # Version und Blob in einer Abfrage laden (vorher zwei ``db.get``-Aufrufe);
    # der Outer-Join trennt fehlende Version (404) von fehlendem Blob (500).
    row = db.execute(
        select(Version, Blob)
        .outerjoin(Blob, Version.blob_id == Blob.id)
        .where(Version.id == version_id)
    ).first()
    if row is None or row[0].entry_id != entry.id:
        raise NotFound("Version nicht gefunden")
    blob = row[1]
    if blob is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Blob fehlt")
    return stream_blob(db, entry, blob, request, download=True, actor_id=user.id)
