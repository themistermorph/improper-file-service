"""Öffentliche Veröffentlichungen (Galerie) und deren Verwaltung.

Eine Veröffentlichung stellt eine **Datei oder einen Ordner** ohne Anmeldung
unter ``/published`` bereit. Ordner werden beim Download als ZIP geliefert (wie
bei Freigaben). Die Veröffentlichung ist bewusst schlanker als eine Freigabe
(kein Passwort, kein Ablauf, kein Download-Limit) und wird über
``published_entries`` geführt. Die Berechtigung entspricht dem Erstellen eines
Freigabelinks: ``read`` **und** ``share`` am Eintrag.
"""

from __future__ import annotations

import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import archive, audit, authz, namespace, ratelimit
from ..errors import PermissionDenied
from ..models import Entry, EntryType, PublishedEntry, User
from ..utils import content_disposition
from .deps import client_ip, get_current_user, get_db
from .entries import stream_entry_content
from .schemas import PublishedOut, PublishedPublicOut

router = APIRouter(tags=["published"])

# Bind-Parameter je IN-Query begrenzen (SQLite alte Builds: max. 999).
_IN_CHUNK_SIZE = 500


def _load_publishers(db: Session, user_ids: set[UUID]) -> dict[UUID, User]:
    """Lädt die Ersteller-Namen gebündelt (statt N+1-Lazy-Loads)."""
    users: dict[UUID, User] = {}
    ids = [user_id for user_id in user_ids if user_id is not None]
    for start in range(0, len(ids), _IN_CHUNK_SIZE):
        chunk = ids[start : start + _IN_CHUNK_SIZE]
        for user in db.execute(select(User).where(User.id.in_(chunk))).scalars().all():
            users[user.id] = user
    return users


def _to_public(published: PublishedEntry, entry: Entry, username: str | None) -> PublishedPublicOut:
    return PublishedPublicOut(
        entry_id=entry.id,
        name=entry.name,
        type=entry.type,
        size=entry.size,
        mime=entry.mime,
        published_by_username=username,
        published_at=published.created_at,
    )


def _to_out(
    db: Session,
    published: PublishedEntry,
    entry: Entry,
    username: str | None,
    *,
    include_path: bool = True,
) -> PublishedOut:
    return PublishedOut(
        entry_id=entry.id,
        name=entry.name,
        type=entry.type,
        size=entry.size,
        mime=entry.mime,
        path=namespace.path_of(db, entry) if include_path else None,
        published_by=published.published_by,
        published_by_username=username,
        published_at=published.created_at,
    )


@router.get("/published", response_model=list[PublishedPublicOut])
def list_published(
    q: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[PublishedPublicOut]:
    """Öffentliche Galerie – ohne Anmeldung erreichbar."""
    stmt = (
        select(PublishedEntry, Entry)
        .join(Entry, Entry.id == PublishedEntry.entry_id)
        .where(Entry.trashed_at.is_(None))
    )
    if q:
        # LIKE-Wildcards (%/_/\) escapen, damit die Suche den Begriff literal
        # interpretiert; ``escape="\\"`` funktioniert mit SQLite und PostgreSQL.
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Entry.name.ilike(f"%{escaped}%", escape="\\"))
    stmt = (
        stmt.order_by(PublishedEntry.created_at.desc()).limit(limit).offset(offset)
    )
    rows = db.execute(stmt).all()
    publishers = _load_publishers(db, {published.published_by for published, _ in rows})
    return [
        _to_public(published, entry, publishers[published.published_by].username
                   if published.published_by in publishers else None)
        for published, entry in rows
    ]


@router.get("/published/mine", response_model=list[PublishedOut])
def list_my_published(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[PublishedOut]:
    """Eigene Veröffentlichungen (Systemadmins sehen alle)."""
    stmt = (
        select(PublishedEntry, Entry)
        .join(Entry, Entry.id == PublishedEntry.entry_id)
        .where(Entry.trashed_at.is_(None))
        .order_by(PublishedEntry.created_at.desc())
    )
    if not authz.is_system_admin(db, user):
        stmt = stmt.where(PublishedEntry.published_by == user.id)
    rows = db.execute(stmt).all()
    publishers = _load_publishers(db, {published.published_by for published, _ in rows})
    return [
        _to_out(
            db,
            published,
            entry,
            publishers[published.published_by].username
            if published.published_by in publishers else None,
            # Systemadmins sehen fremde Veroeffentlichungen ohne vollen Pfad:
            # der virtuelle Pfad ist nur fuer eigene Eintraege bestimmt.
            include_path=published.published_by == user.id,
        )
        for published, entry in rows
    ]


def _serve_folder(db: Session, entry: Entry, request: Request) -> Response:
    """Streamt den veröffentlichten Ordner samt Teilbaum als ZIP."""
    audit.record(
        db, "publish.download", target_entry=entry.id, protocol="http",
        ip=client_ip(request), details={"folder": True},
    )
    path = archive.build_zip(db, [entry])
    size = os.path.getsize(path)
    headers = {
        "Content-Type": "application/zip",
        "Content-Length": str(size),
        "Content-Disposition": content_disposition(f"{entry.name}.zip"),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }

    def folder_iterator():
        try:
            with open(path, "rb") as handle:
                yield from iter(lambda: handle.read(1024 * 1024), b"")
        finally:
            archive.remove_zip(path)

    return StreamingResponse(folder_iterator(), media_type="application/zip", headers=headers)


@router.get("/published/{entry_id}/content")
def download_published(
    entry_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """Öffentlicher Download einer veröffentlichten Datei bzw. eines Ordners (ZIP)."""
    published = db.get(PublishedEntry, entry_id)
    if published is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eintrag ist nicht veröffentlicht")
    entry = db.get(Entry, entry_id)
    if entry is None or entry.trashed_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eintrag ist nicht verfügbar")
    # DoS-Schutz: anonyme Downloads pro Client-IP+Eintrag begrenzen. Gilt fuer
    # Dateien und Ordner (ZIP). Audit-Eintraege bleiben unveraendert.
    key = f"{client_ip(request) or 'unknown'}|{entry_id}"
    if not ratelimit.allow_published_download(key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Zu viele Downloads – bitte später erneut versuchen.",
            headers={"Retry-After": str(ratelimit.published_download_retry_after(key))},
        )
    if entry.type == EntryType.folder:
        return _serve_folder(db, entry, request)
    return stream_entry_content(db, entry, request, download=True)


@router.post(
    "/published/{entry_id}", response_model=PublishedOut, status_code=status.HTTP_201_CREATED
)
def publish_entry(
    entry_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PublishedOut:
    entry = namespace.get_entry(db, entry_id)
    # Die Wurzel ist kein sinnvolles oeffentliches Ziel (sie umfasst den
    # gesamten eigenen Baum) und wird daher nicht veroeffentlicht.
    if entry.parent_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Die Wurzel kann nicht veröffentlicht werden"
        )
    # `share` allein reicht nicht: Wer den Inhalt nicht lesen darf, darf ihn auch
    # nicht öffentlich zugänglich machen (sonst wäre `share` faktisch `read`).
    authz.authorize(db, user, "read", entry)
    authz.authorize(db, user, "share", entry)

    published = db.get(PublishedEntry, entry.id)
    if published is None:
        published = PublishedEntry(entry_id=entry.id, published_by=user.id)
        db.add(published)
        db.flush()
        audit.record(
            db, "publish.create", actor_id=user.id, target_entry=entry.id,
            protocol="http", ip=client_ip(request), details={"entry_id": str(entry.id)},
        )
    # Bei idempotentem Re-Publish den tatsaechlichen Ersteller nennen, nicht den
    # aktuell anfragenden Benutzer (z. B. Admin).
    publisher = db.get(User, published.published_by)
    return _to_out(
        db, published, entry, publisher.username if publisher else user.username
    )


@router.delete("/published/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def unpublish_entry(
    entry_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    published = db.get(PublishedEntry, entry_id)
    if published is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Datei ist nicht veröffentlicht")
    entry = db.get(Entry, entry_id)
    # Auch nach dem Verschieben in den Papierkorb darf die Veröffentlichung
    # widerrufen werden; verwalten darf sie der Ersteller, wer `share` am Eintrag
    # hat oder (als Missbrauchsschutz) ein Systemadmin.
    manageable = published.published_by == user.id
    if not manageable and entry is not None:
        manageable = authz.can(db, user, "share", entry)
    if not manageable and not authz.is_system_admin(db, user):
        raise PermissionDenied("Kein Recht, diese Veröffentlichung zu verwalten")
    db.delete(published)
    audit.record(
        db, "publish.delete", actor_id=user.id, target_entry=entry_id,
        protocol="http", ip=client_ip(request), details={"entry_id": str(entry_id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
