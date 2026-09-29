"""Resumable Upload-Sessions (tus-ähnlich).

Chunks werden in eine Temp-Datei gespoolt und beim Abschluss als Blob in S3
überführt. Für den internen Maßstab bewusst einfach gehalten (siehe ARCHITEKTUR,
Abschnitt 13); ein direkter Multipart-Upload nach S3 wäre der nächste Ausbau.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import BadRequest, Conflict, NotFound, QuotaExceeded
from ..models import Entry, EntryType, UploadSession, UploadStatus, User
from ..utils import uuid7
from . import content, events, namespace


def _spool_dir() -> Path:
    path = Path(get_settings().upload_spool_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_session(
    db: Session,
    parent: Entry,
    name: str,
    owner_id: UUID,
    *,
    size_expected: int | None = None,
    sha256_expected: str | None = None,
    protocol: str = "http",
) -> UploadSession:
    namespace.validate_name(name)
    if parent.type != EntryType.folder:
        raise BadRequest("Ziel ist kein Verzeichnis")
    spool_path = _spool_dir() / f"{uuid7()}.part"
    spool_path.touch()
    session = UploadSession(
        owner_id=owner_id,
        parent_id=parent.id,
        name=name,
        size_expected=size_expected,
        sha256_expected=sha256_expected,
        spool_path=str(spool_path),
        protocol=protocol,
    )
    db.add(session)
    db.flush()
    return session


def get_session(db: Session, session_id: UUID, *, for_update: bool = False) -> UploadSession:
    if for_update:
        # Serialisiert parallele PATCH/complete-Aufrufe derselben Session
        # (Postgres: Zeilensperre; SQLite ignoriert FOR UPDATE).
        session = db.execute(
            select(UploadSession)
            .where(UploadSession.id == session_id)
            .with_for_update()
        ).scalars().first()
    else:
        session = db.get(UploadSession, session_id)
    if session is None or session.status == UploadStatus.aborted:
        raise NotFound("Upload-Session nicht gefunden")
    return session


def begin_append(db: Session, session: UploadSession, offset: int) -> str:
    """Validiert einen Append-Vorgang und liefert den Spool-Pfad."""
    if session.status != UploadStatus.pending:
        raise Conflict("Upload-Session ist nicht mehr offen")
    if offset != session.offset:
        raise Conflict(f"Offset {offset} stimmt nicht mit erwartetem {session.offset} überein")
    # Spool auf den erwarteten Offset normalisieren: Ein fehlender Spool (z. B.
    # nach externem Aufräumen) oder ein zu kleiner Spool würde sonst still zu
    # kurze Dateien erzeugen; überzählige Bytes aus abgebrochenen Versuchen
    # werden verworfen, damit ein Retry keine doppelten Bytes anhängt.
    if not os.path.exists(session.spool_path):
        raise Conflict("Upload-Spool fehlt")
    spool_size = os.path.getsize(session.spool_path)
    if spool_size < offset:
        raise Conflict("Spool kleiner als der bekannte Offset")
    if spool_size > offset:
        os.truncate(session.spool_path, offset)
    return session.spool_path


def finish_append(db: Session, session: UploadSession, written: int) -> UploadSession:
    session.offset += written
    limit = get_settings().max_upload_size
    if limit and session.offset > limit:
        abort(db, session)
        raise QuotaExceeded("Maximale Dateigröße überschritten")
    db.flush()
    return session


def append(db: Session, session: UploadSession, offset: int, chunks: Iterable[bytes]) -> UploadSession:
    if session.status != UploadStatus.pending:
        raise Conflict("Upload-Session ist nicht mehr offen")
    if offset != session.offset:
        raise Conflict(f"Offset {offset} stimmt nicht mit erwartetem {session.offset} überein")

    written = 0
    with open(session.spool_path, "ab") as handle:
        for chunk in chunks:
            if not chunk:
                continue
            handle.write(chunk)
            written += len(chunk)
    session.offset += written

    limit = get_settings().max_upload_size
    if limit and session.offset > limit:
        abort(db, session)
        raise QuotaExceeded("Maximale Dateigröße überschritten")
    db.flush()
    return session


def complete(db: Session, user: User, session: UploadSession, mime: str | None = None) -> Entry:
    if session.status != UploadStatus.pending:
        raise Conflict("Upload-Session ist nicht mehr offen")
    parent = namespace.get_entry(db, session.parent_id)

    if session.size_expected is not None and session.offset != session.size_expected:
        raise BadRequest(
            f"Empfangene Größe {session.offset} != erwartete Größe {session.size_expected}"
        )

    # Der Offset ist die verlässliche Quelle für die tatsächlich akzeptierte
    # Größe: ein manipulierter oder teilweise geschriebener Spool (z. B. nach
    # Client-Abbruch) darf nicht als vollständige Datei durchgehen.
    spool_size = (
        os.path.getsize(session.spool_path) if os.path.exists(session.spool_path) else -1
    )
    if spool_size != session.offset:
        raise BadRequest(
            f"Spool-Größe {spool_size} stimmt nicht mit dem Upload-Offset "
            f"{session.offset} überein"
        )

    # SHA- und Vorabprüfungen **vor** dem S3-Upload, damit bei erwartbaren
    # Fehlern kein verwaistes Objekt im Objektspeicher zurückbleibt.
    if session.sha256_expected:
        actual = content.sha256_file(session.spool_path)
        if actual.lower() != session.sha256_expected.lower():
            raise BadRequest("SHA-256-Prüfsumme stimmt nicht mit der erwarteten überein")
    namespace.ensure_writable(db, parent, session.name, user.id, spool_size)

    blob = content.store_blob(db, session.spool_path, mime)
    entry = namespace.apply_write(db, parent, session.name, user.id, blob, mime)
    session.status = UploadStatus.completed
    _cleanup(session)
    db.flush()
    events.emit(
        db,
        "upload.completed",
        {"entry_id": str(entry.id), "upload_id": str(session.id), "size": blob.size},
    )
    return entry


def abort(db: Session, session: UploadSession) -> None:
    session.status = UploadStatus.aborted
    _cleanup(session)
    db.flush()


def _cleanup(session: UploadSession) -> None:
    try:
        if session.spool_path and os.path.exists(session.spool_path):
            os.remove(session.spool_path)
    except OSError:
        pass
