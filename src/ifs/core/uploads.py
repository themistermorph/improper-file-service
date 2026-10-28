"""Resumable Upload-Sessions (tus-ähnlich).

Klassische Sessions schreiben Chunks in eine Temp-Datei und überführen sie beim
Abschluss als Blob in S3. Für große Dateien gibt es zusätzlich **parallele
Part-Sessions**: Jeder Part landet atomar in einer eigenen Spool-Datei, die
Reihenfolge ist beliebig, und die S3-Finalisierung liest die Parts direkt über
``content.PartReader`` – ohne eine weitere vollständige Kopie anzulegen.
Ein direkter S3-Multipart-Upload schon während des Empfangs bleibt der nächste
Ausbau (der CAS-Schlüssel ist erst nach dem SHA-256 bekannt).
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
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


def part_size() -> int:
    """Part-Größe für parallele Uploads (S3-Multipart-tauglich geklemmt)."""
    value = get_settings().http_upload_part_bytes
    return max(content.MIN_MULTIPART_PART, min(content.MAX_UPLOAD_PART, int(value)))


def _marker_path(session: UploadSession) -> str:
    return f"{session.spool_path}.parts.json"


def _part_path(session: UploadSession, number: int) -> str:
    return f"{session.spool_path}.p{number}"


def _write_marker(spool_path: str, part_size_value: int, total: int) -> None:
    """Schreibt den Part-Marker atomar (kein halb sichtbarer Zustand)."""
    path = f"{spool_path}.parts.json"
    tmp = f"{path}.{os.getpid()}.{uuid7()}.tmp"
    data = {"version": 1, "part_size": int(part_size_value), "total": int(total)}
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, separators=(",", ":"))
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def load_marker(session: UploadSession) -> dict | None:
    """Liest den Part-Marker; ``None`` = klassischer sequenzieller Upload."""
    path = _marker_path(session)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise BadRequest("Part-Marker ist beschädigt") from exc
    try:
        version = int(data.get("version", 0))
        size = int(data["part_size"])
        total = int(data["total"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BadRequest("Part-Marker ist ungültig") from exc
    if version != 1 or size <= 0 or total < 0:
        raise BadRequest("Part-Marker ist ungültig")
    return {"part_size": size, "total": total}


def is_part_session(session: UploadSession) -> bool:
    return os.path.exists(_marker_path(session))


def session_part_size(session: UploadSession) -> int | None:
    marker = load_marker(session)
    return marker["part_size"] if marker else None


def part_count(marker: dict) -> int:
    total = marker["total"]
    size = marker["part_size"]
    return (total + size - 1) // size if total else 0


def expected_part_size(marker: dict, number: int) -> int:
    total = marker["total"]
    size = marker["part_size"]
    return min(size, total - (number - 1) * size)


def received_parts(session: UploadSession, marker: dict) -> list[int]:
    """Liste der vollständig vorhandenen Parts (1-basiert)."""
    result: list[int] = []
    for number in range(1, part_count(marker) + 1):
        path = _part_path(session, number)
        try:
            if os.stat(path).st_size == expected_part_size(marker, number):
                result.append(number)
        except OSError:
            continue
    return result


def contiguous_offset(session: UploadSession, marker: dict) -> int:
    offset = 0
    for number in range(1, part_count(marker) + 1):
        path = _part_path(session, number)
        try:
            if os.stat(path).st_size != expected_part_size(marker, number):
                break
        except OSError:
            break
        offset += expected_part_size(marker, number)
    return offset


@dataclass(frozen=True)
class PartSpec:
    path: str
    size: int
    already_complete: bool


@dataclass(frozen=True)
class UploadState:
    offset: int
    length: int
    part_size: int | None
    parts: list[int]


def create_session(
    db: Session,
    parent: Entry,
    name: str,
    owner_id: UUID,
    *,
    size_expected: int | None = None,
    sha256_expected: str | None = None,
    protocol: str = "http",
    parallel: bool = False,
) -> UploadSession:
    namespace.validate_name(name)
    if parent.type != EntryType.folder:
        raise BadRequest("Ziel ist kein Verzeichnis")
    if parallel and not size_expected:
        raise BadRequest("Paralleler Upload benötigt die erwartete Dateigröße")
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
    if parallel:
        try:
            _write_marker(str(spool_path), part_size(), int(size_expected))
        except Exception:
            # Kein halber Zustand: Session und Spool nicht weiterverwenden.
            db.delete(session)
            db.flush()
            try:
                os.remove(spool_path)
            except OSError:
                pass
            raise
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


def _spool_size(path: str) -> int:
    """Größe des Spools oder -1, wenn er fehlt/verschwunden ist.

    Ein einzelner `os.stat` ersetzt die bisherige exists()+getsize()-Kombination
    (zwei Syscalls) und behält deren Verhalten bei: fehlt die Datei, gilt sie
    als nicht vorhanden.
    """
    try:
        return os.stat(path).st_size
    except OSError:
        return -1


def part_target(db: Session, session: UploadSession, number: int) -> PartSpec:
    """Validiert einen Part-Upload und liefert Zielpfad und erwartete Größe."""
    if session.status != UploadStatus.pending:
        raise Conflict("Upload-Session ist nicht mehr offen")
    marker = load_marker(session)
    if marker is None:
        raise Conflict("Upload-Session ist kein paralleler Upload")
    count = part_count(marker)
    if number < 1 or number > count:
        raise BadRequest(f"Part {number} liegt außerhalb von 1..{count}")
    size = expected_part_size(marker, number)
    path = _part_path(session, number)
    try:
        existing = os.stat(path).st_size
    except OSError:
        existing = None
    if existing is not None and existing != size:
        # Eine vorhandene, aber falsch große Datei ist kein gültiger Retry:
        # entweder wurde die Konfiguration geändert oder der Spool ist defekt.
        raise Conflict(f"Part {number} hat {existing} statt {size} Bytes")
    return PartSpec(path=path, size=size, already_complete=existing == size)


def new_part_temp(spec: PartSpec) -> str:
    directory = os.path.dirname(spec.path) or "."
    fd, path = tempfile.mkstemp(
        prefix=os.path.basename(spec.path) + ".", suffix=".tmp", dir=directory
    )
    os.close(fd)
    return path


def commit_part(spec: PartSpec, temp_path: str) -> None:
    """Übernimmt eine fertig geschriebene Part-Datei atomar."""
    size = os.stat(temp_path).st_size
    if size != spec.size:
        raise BadRequest(f"Part-Größe {size} != erwartet {spec.size}")
    os.replace(temp_path, spec.path)


def state(session: UploadSession) -> UploadState:
    """Aktueller Upload-Zustand für ``HEAD /api/uploads/{id}``."""
    marker = load_marker(session)
    if marker is None:
        return UploadState(
            offset=session.offset,
            length=session.size_expected or session.offset,
            part_size=None,
            parts=[],
        )
    return UploadState(
        offset=contiguous_offset(session, marker),
        length=marker["total"],
        part_size=marker["part_size"],
        parts=received_parts(session, marker),
    )


def begin_append(db: Session, session: UploadSession, offset: int) -> str:
    """Validiert einen Append-Vorgang und liefert den Spool-Pfad."""
    if session.status != UploadStatus.pending:
        raise Conflict("Upload-Session ist nicht mehr offen")
    if is_part_session(session):
        raise Conflict("Paralleler Upload: bitte den Part-Endpunkt verwenden")
    if offset != session.offset:
        raise Conflict(f"Offset {offset} stimmt nicht mit erwartetem {session.offset} überein")
    # Spool auf den erwarteten Offset normalisieren: Ein fehlender Spool (z. B.
    # nach externem Aufräumen) oder ein zu kleiner Spool würde sonst still zu
    # kurze Dateien erzeugen; überzählige Bytes aus abgebrochenen Versuchen
    # werden verworfen, damit ein Retry keine doppelten Bytes anhängt.
    spool_size = _spool_size(session.spool_path)
    if spool_size < 0:
        raise Conflict("Upload-Spool fehlt")
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
    if is_part_session(session):
        raise Conflict("Paralleler Upload: bitte den Part-Endpunkt verwenden")
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
    marker = load_marker(session)
    if marker is not None:
        return _complete_parts(db, user, session, marker, mime)
    parent = namespace.get_entry(db, session.parent_id)

    if session.size_expected is not None and session.offset != session.size_expected:
        raise BadRequest(
            f"Empfangene Größe {session.offset} != erwartete Größe {session.size_expected}"
        )

    # Der Offset ist die verlässliche Quelle für die tatsächlich akzeptierte
    # Größe: ein manipulierter oder teilweise geschriebener Spool (z. B. nach
    # Client-Abbruch) darf nicht als vollständige Datei durchgehen.
    spool_size = _spool_size(session.spool_path)
    if spool_size != session.offset:
        raise BadRequest(
            f"Spool-Größe {spool_size} stimmt nicht mit dem Upload-Offset "
            f"{session.offset} überein"
        )

    # SHA- und Vorabprüfungen **vor** dem S3-Upload, damit bei erwartbaren
    # Fehlern kein verwaistes Objekt im Objektspeicher zurückbleibt.
    actual: str | None = None
    if session.sha256_expected:
        actual = content.sha256_file(session.spool_path)
        if actual.lower() != session.sha256_expected.lower():
            raise BadRequest("SHA-256-Prüfsumme stimmt nicht mit der erwarteten überein")
    namespace.ensure_writable(db, parent, session.name, user.id, spool_size)

    if actual is None:
        blob = content.store_blob(db, session.spool_path, mime)
    else:
        # Der Digest wurde oben bereits über die ganze Datei berechnet und
        # gegen den Client-Wert geprüft – store_blob muss nicht erneut hashen.
        blob = content.store_blob(db, session.spool_path, mime, sha256=actual)
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


def _complete_parts(
    db: Session,
    user: User,
    session: UploadSession,
    marker: dict,
    mime: str | None,
) -> Entry:
    """Schließt eine parallele Session ab (Parts prüfen, hashen, nach S3)."""
    parent = namespace.get_entry(db, session.parent_id)
    total = marker["total"]
    if session.size_expected is not None and total != session.size_expected:
        raise BadRequest(
            f"Part-Gesamtgröße {total} != erwartete Größe {session.size_expected}"
        )

    paths: list[str] = []
    for number in range(1, part_count(marker) + 1):
        path = _part_path(session, number)
        expected = expected_part_size(marker, number)
        try:
            size = os.stat(path).st_size
        except OSError:
            raise BadRequest(f"Part {number} fehlt")
        if size != expected:
            raise BadRequest(
                f"Part {number} hat {size} statt {expected} Bytes"
            )
        paths.append(path)

    # SHA- und Vorabprüfungen vor dem S3-Upload (kein verwaistes Objekt bei
    # erwartbaren Fehlern). Der Digest wird genau einmal über die Parts gebildet.
    actual: str | None = None
    if session.sha256_expected:
        actual = content.sha256_parts(paths)
        if actual.lower() != session.sha256_expected.lower():
            raise BadRequest("SHA-256-Prüfsumme stimmt nicht mit der erwarteten überein")
    namespace.ensure_writable(db, parent, session.name, user.id, total)

    if total == 0:
        # Leere Datei: kein Part-Objekt nötig; der leere Basis-Spool genügt.
        blob = content.store_blob(db, session.spool_path, mime)
    else:
        blob = content.store_blob_parts(db, paths, mime, sha256=actual)
    entry = namespace.apply_write(db, parent, session.name, user.id, blob, mime)
    session.offset = total
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


def cleanup(session: UploadSession) -> None:
    """Entfernt Spool, Part-Marker und alle Part-/Temp-Dateien einer Session."""
    if not session.spool_path:
        return
    paths = [session.spool_path, _marker_path(session)]
    # Part-Dateien und deren Temp-Dateien heißen <spool>.p<nummer>[.tmp].
    directory = os.path.dirname(session.spool_path) or "."
    prefix = os.path.basename(session.spool_path) + ".p"
    try:
        for entry in os.scandir(directory):
            if entry.name.startswith(prefix):
                paths.append(entry.path)
    except OSError:
        pass
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass


def _cleanup(session: UploadSession) -> None:
    cleanup(session)
