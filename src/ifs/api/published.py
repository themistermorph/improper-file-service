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
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import archive, audit, authz, namespace, ratelimit
from ..errors import PermissionDenied
from ..models import Entry, EntryType, PublishedEntry, User
from ..utils import content_disposition
from .deps import client_ip, get_current_user, get_db
from .entries import stream_entry_content
from .schemas import PublishedInput, PublishedOut, PublishedPublicOut

router = APIRouter(tags=["published"])

# Bind-Parameter je IN-Query begrenzen (SQLite alte Builds: max. 999).
_IN_CHUNK_SIZE = 500

# Vanity-Link: ein URL-sicheres Segment, das der Server um `<username>/` ergänzt.
_PUBLIC_SUFFIX_RE = re.compile(r"^[A-Za-z0-9._~-]+$")
_MAX_PUBLIC_SUFFIX = 200
_MAX_PUBLIC_PATH = 512


def _load_publishers(db: Session, user_ids: set[UUID]) -> dict[UUID, User]:
    """Lädt die Ersteller-Namen gebündelt (statt N+1-Lazy-Loads)."""
    users: dict[UUID, User] = {}
    ids = [user_id for user_id in user_ids if user_id is not None]
    for start in range(0, len(ids), _IN_CHUNK_SIZE):
        chunk = ids[start : start + _IN_CHUNK_SIZE]
        for user in db.execute(select(User).where(User.id.in_(chunk))).scalars().all():
            users[user.id] = user
    return users


def _effective_name(published: PublishedEntry, entry: Entry) -> str:
    """Anzeigename in der Galerie: öffentlicher Name, sonst interner Name."""
    return published.public_name or entry.name


def _clean_public_name(value: str | None) -> str | None:
    """Trimmt den öffentlichen Namen; leer/None bedeutet „internen Namen nutzen"."""
    cleaned = (value or "").strip()
    return cleaned or None


def _clean_public_suffix(value: str | None) -> str | None:
    """Validiert den frei wählbaren Link-Suffix (genau ein Segment, ohne '/')."""
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    if len(cleaned) > _MAX_PUBLIC_SUFFIX or "/" in cleaned:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültiger Link-Suffix")
    if cleaned in (".", "..") or not _PUBLIC_SUFFIX_RE.fullmatch(cleaned):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Link-Suffix: nur A-Z, a-z, 0-9, '.', '_', '~' und '-' (ein Segment)",
        )
    return cleaned


def _suffix_of(public_path: str | None) -> str | None:
    if not public_path:
        return None
    return public_path.rsplit("/", 1)[-1]


def _publisher_user(db: Session, published: PublishedEntry, fallback: User) -> User:
    """Der tatsächliche Ersteller (auch wenn ein Admin nachträglich bearbeitet)."""
    if published.published_by:
        publisher = db.get(User, published.published_by)
        if publisher is not None:
            return publisher
    return fallback


def _build_public_path(username: str, suffix: str) -> str:
    public_path = f"{username}/{suffix}"
    if len(public_path) > _MAX_PUBLIC_PATH:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Link-Pfad zu lang")
    return public_path


def _ensure_public_path_free(db: Session, public_path: str, entry_id: UUID) -> None:
    existing = db.execute(
        select(PublishedEntry.entry_id).where(
            func.lower(PublishedEntry.public_path) == public_path.lower(),
            PublishedEntry.entry_id != entry_id,
        )
    ).scalars().first()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Link-Suffix ist bereits vergeben")


def _set_public_suffix(
    db: Session, published: PublishedEntry, publisher: User, suffix: str | None
) -> None:
    if suffix is None:
        published.public_path = None
        return
    public_path = _build_public_path(publisher.username, suffix)
    _ensure_public_path_free(db, public_path, published.entry_id)
    published.public_path = public_path


def _to_public(
    published: PublishedEntry,
    entry: Entry,
    username: str | None,
    display_name: str | None = None,
) -> PublishedPublicOut:
    return PublishedPublicOut(
        entry_id=entry.id,
        name=_effective_name(published, entry),
        public_path=published.public_path,
        type=entry.type,
        size=entry.size,
        mime=entry.mime,
        published_by_username=username,
        published_by_display_name=display_name,
        published_at=published.created_at,
    )


def _to_out(
    db: Session,
    published: PublishedEntry,
    entry: Entry,
    username: str | None,
    display_name: str | None = None,
    *,
    include_path: bool = True,
) -> PublishedOut:
    return PublishedOut(
        entry_id=entry.id,
        name=entry.name,
        public_name=published.public_name,
        public_path=published.public_path,
        public_suffix=_suffix_of(published.public_path),
        type=entry.type,
        size=entry.size,
        mime=entry.mime,
        path=namespace.path_of(db, entry) if include_path else None,
        published_by=published.published_by,
        published_by_username=username,
        published_by_display_name=display_name,
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
    result: list[PublishedPublicOut] = []
    for published, entry in rows:
        publisher = publishers.get(published.published_by)
        result.append(
            _to_public(
                published,
                entry,
                publisher.username if publisher else None,
                publisher.display_name if publisher else None,
            )
        )
    return result


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
    result: list[PublishedOut] = []
    for published, entry in rows:
        publisher = publishers.get(published.published_by)
        result.append(
            _to_out(
                db,
                published,
                entry,
                publisher.username if publisher else None,
                publisher.display_name if publisher else None,
                # Systemadmins sehen fremde Veroeffentlichungen ohne vollen Pfad:
                # der virtuelle Pfad ist nur fuer eigene Eintraege bestimmt.
                include_path=published.published_by == user.id,
            )
        )
    return result


def _serve_folder(db: Session, entry: Entry, request: Request, download_name: str) -> Response:
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
        "Content-Disposition": content_disposition(f"{download_name}.zip"),
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
    download_name = _effective_name(published, entry)
    if entry.type == EntryType.folder:
        return _serve_folder(db, entry, request, download_name)
    return stream_entry_content(
        db, entry, request, download=True, download_name=download_name
    )


@router.post(
    "/published/{entry_id}", response_model=PublishedOut, status_code=status.HTTP_201_CREATED
)
def publish_entry(
    entry_id: UUID,
    request: Request,
    payload: PublishedInput | None = None,
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

    public_name = _clean_public_name(payload.public_name) if payload else None
    suffix = (
        _clean_public_suffix(payload.public_suffix)
        if payload is not None and "public_suffix" in payload.model_fields_set
        else None
    )
    published = db.get(PublishedEntry, entry.id)
    if published is None:
        published = PublishedEntry(
            entry_id=entry.id, published_by=user.id, public_name=public_name
        )
        if suffix is not None:
            _set_public_suffix(db, published, user, suffix)
        db.add(published)
        db.flush()
        audit.record(
            db, "publish.create", actor_id=user.id, target_entry=entry.id,
            protocol="http", ip=client_ip(request),
            details={"entry_id": str(entry.id), "public_path": published.public_path},
        )
    else:
        changed = False
        if payload is not None and payload.public_name is not None:
            # Idempotentes Re-Publish darf den Anzeigenamen aktualisieren.
            published.public_name = public_name
            changed = True
        if payload is not None and "public_suffix" in payload.model_fields_set:
            _set_public_suffix(db, published, _publisher_user(db, published, user), suffix)
            changed = True
        if changed:
            db.flush()
            audit.record(
                db, "publish.update", actor_id=user.id, target_entry=entry.id,
                protocol="http", ip=client_ip(request),
                details={"entry_id": str(entry.id), "public_path": published.public_path},
            )
    # Bei idempotentem Re-Publish den tatsaechlichen Ersteller nennen, nicht den
    # aktuell anfragenden Benutzer (z. B. Admin).
    publisher = db.get(User, published.published_by)
    return _to_out(
        db,
        published,
        entry,
        publisher.username if publisher else user.username,
        publisher.display_name if publisher else user.display_name,
    )


def _can_manage(
    db: Session, user: User, published: PublishedEntry, entry: Entry | None
) -> bool:
    """Verwaltungsrecht: Ersteller, `share`-Berechtigte oder Systemadmin."""
    if published.published_by == user.id:
        return True
    if entry is not None and authz.can(db, user, "share", entry):
        return True
    return authz.is_system_admin(db, user)


@router.patch("/published/{entry_id}", response_model=PublishedOut)
def update_published(
    entry_id: UUID,
    payload: PublishedInput,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PublishedOut:
    """Ändert den öffentlichen Anzeigenamen nachträglich (leer = interner Name)."""
    published = db.get(PublishedEntry, entry_id)
    if published is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eintrag ist nicht veröffentlicht")
    # Nur aktive Einträge lassen sich umbenennen (trash-Aufräumen bleibt dem
    # separaten Widerruf vorbehalten).
    entry = namespace.get_entry(db, entry_id)
    if not _can_manage(db, user, published, entry):
        raise PermissionDenied("Kein Recht, diese Veröffentlichung zu verwalten")
    changed = False
    if "public_name" in payload.model_fields_set:
        published.public_name = _clean_public_name(payload.public_name)
        changed = True
    if "public_suffix" in payload.model_fields_set:
        suffix = _clean_public_suffix(payload.public_suffix)
        _set_public_suffix(db, published, _publisher_user(db, published, user), suffix)
        changed = True
    if changed:
        db.flush()
        audit.record(
            db, "publish.update", actor_id=user.id, target_entry=entry_id,
            protocol="http", ip=client_ip(request),
            details={"entry_id": str(entry_id), "public_path": published.public_path},
        )
    publisher = db.get(User, published.published_by) if published.published_by else None
    return _to_out(
        db,
        published,
        entry,
        publisher.username if publisher else user.username,
        publisher.display_name if publisher else user.display_name,
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
    if not _can_manage(db, user, published, entry):
        raise PermissionDenied("Kein Recht, diese Veröffentlichung zu verwalten")
    db.delete(published)
    audit.record(
        db, "publish.delete", actor_id=user.id, target_entry=entry_id,
        protocol="http", ip=client_ip(request), details={"entry_id": str(entry_id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
