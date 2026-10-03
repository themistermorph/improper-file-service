"""Namespace: Verzeichnisbaum, Dateien, Versionen, Rename/Move, Papierkorb."""

from __future__ import annotations

import mimetypes
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from ..errors import BadRequest, Conflict, NotFound
from ..models import Blob, Entry, EntryType, PublishedEntry, Share, User, Version
from ..utils import normalize_path, safe_mime, utcnow
from . import authz, events, quota

ROOT_NAME = "/"
MAX_NAME_LENGTH = 255
_FORBIDDEN_CHARS = {"/", "\\", "\x00"}


def validate_name(name: str) -> str:
    if not name or name in (".", ".."):
        raise BadRequest("Ungültiger Name")
    if len(name) > MAX_NAME_LENGTH:
        raise BadRequest("Name zu lang")
    if any(char in name for char in _FORBIDDEN_CHARS):
        raise BadRequest("Name enthält unerlaubte Zeichen")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in name):
        # Steuerzeichen (inkl. CR/LF) verhindern Header-Injection.
        raise BadRequest("Name enthält Steuerzeichen")
    return name


def get_root(db: Session, owner_id: UUID) -> Entry | None:
    """Wurzel (Home) eines Benutzers – genau eine je Besitzer."""
    return db.execute(
        select(Entry).where(
            Entry.parent_id.is_(None),
            Entry.type == EntryType.folder,
            Entry.owner_id == owner_id,
            Entry.trashed_at.is_(None),
        )
    ).scalars().first()


def ensure_root(db: Session, owner_id: UUID) -> Entry:
    """Liefert die Wurzel des Benutzers oder legt sie an (idempotent)."""
    root = get_root(db, owner_id)
    if root is not None:
        return root
    root = Entry(name=ROOT_NAME, type=EntryType.folder, owner_id=owner_id, parent_id=None)
    try:
        with db.begin_nested():
            db.add(root)
            db.flush()
    except IntegrityError:
        # Race: parallel angelegt -> vorhandene Wurzel verwenden.
        existing = get_root(db, owner_id)
        if existing is None:
            raise
        return existing
    return root


def find_child(db: Session, parent_id: UUID, name: str) -> Entry | None:
    return db.execute(
        select(Entry).where(
            Entry.parent_id == parent_id,
            func.lower(Entry.name) == name.lower(),
            Entry.trashed_at.is_(None),
        )
    ).scalars().first()


def unique_child_name(db: Session, parent: Entry, name: str) -> str:
    """Weicht bei Namenskonflikten auf 'name (2).ext' aus."""
    validate_name(name)
    if find_child(db, parent.id, name) is None:
        return name
    base, dot, ext = name.partition(".")
    suffix = f".{ext}" if dot else ""
    index = 2
    while True:
        candidate = f"{base} ({index}){suffix}"
        if find_child(db, parent.id, candidate) is None:
            return candidate
        index += 1


def resolve_path(db: Session, path: str, root: Entry) -> Entry | None:
    normalized = normalize_path(path)
    if normalized == "/":
        return root
    node: Entry | None = root
    for part in normalized.strip("/").split("/"):
        if node is None:
            return None
        node = find_child(db, node.id, part)
    return node


# Bind-Parameter je IN-Query begrenzen: schützt SQLite (alte Builds: max. 999
# Variablen) und PostgreSQL (max. 65535) vor zu großen Parameterlisten.
_IN_CHUNK_SIZE = 500


def _children_of(
    db: Session, parent_ids: list[UUID], trashed: bool | None = None
) -> list[Entry]:
    """Kinder mehrerer Eltern gebündelt laden (eine Query je Parameter-Chunk).

    Ersetzt die frühere Child-Query je Knoten. ``trashed`` wählt den Filter der
    Aufrufer: ``None`` = alle Kinder, ``False`` = nur aktive, ``True`` = nur
    gelöschte Einträge.
    """
    children: list[Entry] = []
    for start in range(0, len(parent_ids), _IN_CHUNK_SIZE):
        chunk = parent_ids[start : start + _IN_CHUNK_SIZE]
        statement = select(Entry).where(Entry.parent_id.in_(chunk))
        if trashed is True:
            statement = statement.where(Entry.trashed_at.isnot(None))
        elif trashed is False:
            statement = statement.where(Entry.trashed_at.is_(None))
        children.extend(db.execute(statement).scalars().all())
    return children


def path_of(db: Session, entry: Entry) -> str:
    """Pfad des Eintrags ("/a/b"); die Wurzel ergibt "/".

    Die Ahnenkette wird in einer Query per rekursiver CTE geladen (statt einer
    Lazy-Load-Query je Ebene). Das ``UNION`` (nicht ``UNION ALL``) dedupliziert
    identische Knotenzeilen, damit ein bereits vorhandener Zyklus die Query
    terminieren lässt; die ``seen``-Menge unten bricht zusätzlich exakt wie
    zuvor beim ersten Wiederholungsknoten ab.
    """
    ancestors = (
        select(Entry.id, Entry.parent_id, Entry.name)
        .where(Entry.id == entry.id)
        .cte("ancestors", recursive=True)
    )
    parent = aliased(Entry)
    ancestors = ancestors.union(
        select(parent.id, parent.parent_id, parent.name).where(
            parent.id == ancestors.c.parent_id, ancestors.c.parent_id.isnot(None)
        )
    )
    rows = db.execute(
        select(ancestors.c.id, ancestors.c.parent_id, ancestors.c.name)
    ).all()
    by_id = {row.id: row for row in rows}
    parts: list[str] = []
    seen: set[UUID] = set()
    current: UUID | None = entry.id
    while current is not None:
        row = by_id.get(current)
        if row is None or row.parent_id is None:
            break
        if row.id in seen:
            # Defense-in-Depth: Ein Zyklus im Baum (durch die Zeilensperre in
            # ``move`` verhindert) darf hier nicht zur Endlosschleife führen.
            break
        seen.add(row.id)
        parts.append(row.name)
        current = row.parent_id
    return "/" + "/".join(reversed(parts))


def get_entry(db: Session, entry_id: UUID) -> Entry:
    entry = db.get(Entry, entry_id)
    if entry is None or entry.trashed_at is not None:
        raise NotFound("Eintrag nicht gefunden")
    return entry


def get_trashed_entry(db: Session, entry_id: UUID) -> Entry:
    entry = db.get(Entry, entry_id)
    if entry is None or entry.trashed_at is None:
        raise NotFound("Eintrag ist nicht im Papierkorb")
    return entry


def list_children(db: Session, parent: Entry) -> list[Entry]:
    entries = db.execute(
        select(Entry)
        .where(Entry.parent_id == parent.id, Entry.trashed_at.is_(None))
        # Sortierung in SQL statt in Python: Ordner zuerst, dann Name
        # case-insensitiv (identisch zu (type != folder, name.lower())).
        .order_by((Entry.type == EntryType.folder).desc(), func.lower(Entry.name))
    ).scalars().all()
    return list(entries)


def create_folder(db: Session, parent: Entry, name: str, owner_id: UUID) -> Entry:
    validate_name(name)
    if parent.type != EntryType.folder:
        raise BadRequest("Ziel ist kein Verzeichnis")
    if find_child(db, parent.id, name) is not None:
        raise Conflict(f"'{name}' existiert bereits")
    folder = Entry(name=name, type=EntryType.folder, owner_id=owner_id, parent_id=parent.id)
    try:
        with db.begin_nested():
            db.add(folder)
            db.flush()
    except IntegrityError as exc:
        raise Conflict(f"'{name}' existiert bereits") from exc
    events.emit(db, "entry.created", {"entry_id": str(folder.id), "type": "folder"})
    return folder


def _existing_writable(
    db: Session, parent: Entry, name: str, owner_id: UUID, size: int
) -> Entry | None:
    """Gemeinsamer Kern von ``ensure_writable`` und ``apply_write``.

    Validiert Name, Zieltyp, Konflikt und Quota und liefert den bereits
    vorhandenen aktiven Eintrag zurück (oder ``None``), damit ``apply_write``
    ``find_child`` nicht doppelt ausführen muss.
    """
    validate_name(name)
    if parent.type != EntryType.folder:
        raise BadRequest("Ziel ist kein Verzeichnis")
    existing = find_child(db, parent.id, name)
    if existing is not None and existing.type == EntryType.folder:
        raise Conflict(f"'{name}' ist ein Verzeichnis")
    # Beim Überschreiben gehören die ersetzten Bytes dem ursprünglichen Besitzer;
    # angerechnet wird ihm (bzw. dem Schreibenden, falls kein Besitzer existiert)
    # daher nur das Delta.
    quota_owner = owner_id if existing is None else (existing.owner_id or owner_id)
    existing_size = existing.size if existing is not None else 0
    quota.enforce(db, quota_owner, int(size) - existing_size)
    return existing


def ensure_writable(
    db: Session, parent: Entry, name: str, owner_id: UUID, size: int
) -> None:
    """Vorabprüfung **vor** dem S3-Upload: Name, Zieltyp, Konflikt und Quota.

    Verhindert verwaiste Objekte im Objektspeicher, wenn ein Upload sonst erst
    nach dem Hochladen an einem absehbaren Fehler (Namenskonflikt, Quota)
    scheitern würde. Die eigentliche Prüfung erfolgt in ``apply_write`` erneut.
    """
    _existing_writable(db, parent, name, owner_id, size)


def apply_write(
    db: Session,
    parent: Entry,
    name: str,
    owner_id: UUID,
    blob: Blob,
    mime: str | None = None,
) -> Entry:
    """Legt eine Datei an oder ersetzt ihren Inhalt durch eine neue Version."""
    existing = _existing_writable(db, parent, name, owner_id, blob.size)
    if mime is None:
        mime = mimetypes.guess_type(name)[0]
    mime = safe_mime(mime)

    if existing is None:
        entry = Entry(
            name=name,
            type=EntryType.file,
            owner_id=owner_id,
            parent_id=parent.id,
        )
        try:
            with db.begin_nested():
                db.add(entry)
                db.flush()
        except IntegrityError as exc:
            raise Conflict(f"'{name}' existiert bereits") from exc
        seq = 1
    else:
        entry = existing
        last_seq = db.execute(
            select(func.coalesce(func.max(Version.seq), 0)).where(Version.entry_id == entry.id)
        ).scalar_one()
        seq = int(last_seq) + 1

    version = Version(
        entry_id=entry.id,
        blob_id=blob.id,
        seq=seq,
        size=blob.size,
        mime=mime,
        sha256=blob.sha256,
        created_by=owner_id,
    )
    db.add(version)
    db.flush()

    entry.current_version_id = version.id
    entry.size = blob.size
    entry.mime = mime
    entry.sha256 = blob.sha256
    entry.updated_at = utcnow()
    db.flush()

    events.emit(
        db,
        "entry.written",
        {"entry_id": str(entry.id), "version_id": str(version.id), "size": blob.size},
    )
    return entry


def list_versions(db: Session, entry: Entry) -> list[Version]:
    return list(
        db.execute(
            select(Version).where(Version.entry_id == entry.id).order_by(Version.seq.desc())
        ).scalars().all()
    )


def get_version(db: Session, entry: Entry, version_id: UUID) -> Version:
    version = db.get(Version, version_id)
    if version is None or version.entry_id != entry.id:
        raise NotFound("Version nicht gefunden")
    return version


def restore_version(
    db: Session, entry: Entry, version: Version, actor_id: UUID | None = None
) -> Version:
    """Stellt eine frühere Version als *neue* aktuelle Version wieder her (append-only)."""
    if entry.type != EntryType.file:
        raise BadRequest("Nur Dateien haben Versionen")
    # Auch ein Rollback verändert die belegte Speichermenge und muss daher wie
    # jedes andere Schreiben gegen die Quota des Besitzers geprüft werden.
    owner = entry.owner_id or actor_id
    if owner is not None:
        quota.enforce(db, owner, int(version.size) - int(entry.size))
    last_seq = db.execute(
        select(func.coalesce(func.max(Version.seq), 0)).where(Version.entry_id == entry.id)
    ).scalar_one()
    new_version = Version(
        entry_id=entry.id,
        blob_id=version.blob_id,
        seq=int(last_seq) + 1,
        size=version.size,
        mime=version.mime,
        sha256=version.sha256,
        created_by=actor_id,
    )
    db.add(new_version)
    db.flush()

    entry.current_version_id = new_version.id
    entry.size = version.size
    entry.mime = version.mime
    entry.sha256 = version.sha256
    entry.updated_at = utcnow()
    db.flush()

    events.emit(
        db,
        "entry.version_restored",
        {"entry_id": str(entry.id), "version_id": str(new_version.id), "from_seq": version.seq},
    )
    return new_version


def rename(db: Session, entry: Entry, new_name: str) -> Entry:
    validate_name(new_name)
    if entry.parent_id is None:
        raise BadRequest("Wurzel kann nicht umbenannt werden")
    sibling = find_child(db, entry.parent_id, new_name)
    if sibling is not None and sibling.id != entry.id:
        raise Conflict(f"'{new_name}' existiert bereits")
    entry.name = new_name
    entry.updated_at = utcnow()
    db.flush()
    events.emit(db, "entry.renamed", {"entry_id": str(entry.id), "name": new_name})
    return entry


def _is_descendant(entry: Entry, maybe_ancestor_id: UUID) -> bool:
    node: Entry | None = entry
    seen: set[UUID] = set()
    while node is not None:
        if node.id == maybe_ancestor_id:
            return True
        if node.id in seen:
            # Defense-in-Depth gegen bereits vorhandene Zyklen (siehe ``path_of``).
            return False
        seen.add(node.id)
        node = node.parent
    return False


def move(db: Session, entry: Entry, new_parent: Entry) -> Entry:
    if entry.parent_id is None:
        raise BadRequest("Wurzel kann nicht verschoben werden")
    if new_parent.type != EntryType.folder:
        raise BadRequest("Ziel ist kein Verzeichnis")
    # Beide beteiligten Zeilen vor der Zyklusprüfung sperren, damit Prüfung und
    # Update atomar sind. Auf SQLite ist ``FOR UPDATE`` ein No-op; auf PostgreSQL
    # verhindert es parallele Moves (A→B und B→A), die sonst einen Zyklus
    # erzeugen könnten. Feste Sortierung vermeidet Deadlocks; ``populate_existing``
    # liest nach dem Warten auf die Sperre den aktuellen Stand der Zeilen.
    db.execute(
        select(Entry)
        .where(Entry.id.in_([entry.id, new_parent.id]))
        .order_by(Entry.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalars().all()
    if entry.id == new_parent.id or _is_descendant(new_parent, entry.id):
        raise BadRequest("Ein Ordner kann nicht in sich selbst verschoben werden")
    sibling = find_child(db, new_parent.id, entry.name)
    if sibling is not None and sibling.id != entry.id:
        raise Conflict(f"'{entry.name}' existiert im Ziel bereits")
    entry.parent_id = new_parent.id
    entry.updated_at = utcnow()
    db.flush()
    events.emit(db, "entry.moved", {"entry_id": str(entry.id), "parent_id": str(new_parent.id)})
    return entry


def soft_delete(db: Session, entry: Entry, actor_id: UUID | None = None) -> None:
    if entry.parent_id is None:
        raise BadRequest("Wurzel kann nicht gelöscht werden")
    trashed_ids: list[UUID] = []
    _trash_subtree(db, entry, actor_id, trashed_ids)

    # Freigabelinks und Veröffentlichungen auf gelöschte Einträge automatisch
    # widerrufen – das Papierkorb-Item darf nicht in der Galerie auftauchen.
    revoked = 0
    if trashed_ids:
        result = db.execute(delete(Share).where(Share.entry_id.in_(trashed_ids)))
        revoked = result.rowcount or 0
        result = db.execute(
            delete(PublishedEntry).where(PublishedEntry.entry_id.in_(trashed_ids))
        )
        revoked += result.rowcount or 0
        db.flush()

    events.emit(
        db,
        "entry.trashed",
        {"entry_id": str(entry.id), "by": str(actor_id) if actor_id else None, "revoked_shares": revoked},
    )


def _trash_subtree(db: Session, entry: Entry, actor_id: UUID | None, ids: list[UUID]) -> None:
    # Iterativ und ebenenweise statt rekursiv, damit auch sehr tiefe Bäume nicht
    # in einen RecursionError laufen und nur eine Child-Query je Baumebene
    # anfällt. ``seen`` schützt vor Zyklen in inkonsistenten Daten.
    seen: set[UUID] = set()
    frontier: list[Entry] = [entry]
    while frontier:
        level: list[Entry] = []
        for node in frontier:
            if node.id in seen:
                continue
            seen.add(node.id)
            node.trashed_at = utcnow()
            node.trashed_by = actor_id
            if node.original_parent_id is None:
                node.original_parent_id = node.parent_id
            node.updated_at = utcnow()
            ids.append(node.id)
            level.append(node)
        if not level:
            break
        frontier = _children_of(db, [node.id for node in level], trashed=False)
    db.flush()


# --- Papierkorb ---------------------------------------------------------


def _is_top_level_trash(entry: Entry) -> bool:
    """True, wenn der Eintrag selbst im Papierkorb liegt, sein Parent aber nicht."""
    return entry.trashed_at is not None and (
        entry.parent is None or entry.parent.trashed_at is None
    )


def is_top_level_trash(entry: Entry) -> bool:
    """Öffentliche Variante für die API-Schicht."""
    return _is_top_level_trash(entry)


def can_manage_trash(db: Session, user: User, entry: Entry) -> bool:
    return entry.owner_id == user.id or authz.can(db, user, "delete", entry)


def _manageable_top_level_trash(db: Session, user: User) -> list[Entry]:
    """Oberste, für ``user`` verwaltbare Papierkorb-Einträge.

    Ermittelt die ``trashed_at``-Werte der Eltern ohne Lazy-Loads: Eltern, die
    selbst im Papierkorb liegen, stammen aus dem geladenen Ergebnis; fehlende
    Parent-IDs werden gebündelt per ``IN``-Query nachgeschlagen.
    """
    rows = db.execute(select(Entry).where(Entry.trashed_at.isnot(None))).scalars().all()
    trashed_at_by_id = {row.id: row.trashed_at for row in rows}
    missing_parent_ids = list(
        dict.fromkeys(
            row.parent_id
            for row in rows
            if row.parent_id is not None and row.parent_id not in trashed_at_by_id
        )
    )
    parent_trashed_at: dict[UUID, datetime | None] = {}
    for start in range(0, len(missing_parent_ids), _IN_CHUNK_SIZE):
        chunk = missing_parent_ids[start : start + _IN_CHUNK_SIZE]
        for entry_id, trashed_at in db.execute(
            select(Entry.id, Entry.trashed_at).where(Entry.id.in_(chunk))
        ).all():
            parent_trashed_at[entry_id] = trashed_at

    top: list[Entry] = []
    for row in rows:
        if row.parent_id is not None:
            parent_state = (
                trashed_at_by_id[row.parent_id]
                if row.parent_id in trashed_at_by_id
                else parent_trashed_at.get(row.parent_id)
            )
            # Wie ``_is_top_level_trash``: Nur Einträge, deren Parent aktiv,
            # fehlend oder nicht vorhanden ist, sind oberste Papierkorb-Einträge.
            if parent_state is not None:
                continue
        if can_manage_trash(db, user, row):
            top.append(row)
    return top


def list_trash(db: Session, user: User) -> list[Entry]:
    top = _manageable_top_level_trash(db, user)
    top.sort(key=lambda e: e.trashed_at or e.updated_at, reverse=True)
    return top


def trash_count(db: Session, user: User) -> int:
    # Gleiche Filterlogik wie ``list_trash``, ohne die Sortierung.
    return len(_manageable_top_level_trash(db, user))


def subtree_stats(db: Session, entry: Entry) -> tuple[int, int]:
    """Liefert (Dateien, Bytes) im Teilbaum unter dem Eintrag.

    Iterativ und ebenenweise: eine Child-Query je Baumebene statt je Knoten;
    ``seen`` verhindert Mehrfachzählung bei Zyklen.
    """
    files = 0
    total = 0
    seen: set[UUID] = set()
    frontier: list[Entry] = [entry]
    while frontier:
        level: list[Entry] = []
        for node in frontier:
            if node.id in seen:
                continue
            seen.add(node.id)
            level.append(node)
            if node.type == EntryType.file:
                files += 1
                total += node.size
        if not level:
            break
        frontier = _children_of(db, [node.id for node in level], trashed=None)
    return files, total


def _untrash_node(node: Entry) -> None:
    node.trashed_at = None
    node.trashed_by = None
    node.original_parent_id = None
    node.updated_at = utcnow()


def _untrash_subtree(db: Session, entry: Entry) -> None:
    # Iterativ und ebenenweise (siehe _trash_subtree); ``seen`` schützt vor
    # Zyklen in inkonsistenten Daten.
    seen: set[UUID] = set()
    frontier: list[Entry] = [entry]
    while frontier:
        level: list[Entry] = []
        for node in frontier:
            if node.id in seen:
                continue
            seen.add(node.id)
            _untrash_node(node)
            level.append(node)
        if not level:
            break
        frontier = _children_of(db, [node.id for node in level], trashed=True)
    db.flush()


def restore(
    db: Session,
    entry: Entry,
    actor_id: UUID | None = None,
    new_parent: Entry | None = None,
    new_name: str | None = None,
) -> Entry:
    if entry.trashed_at is None:
        raise BadRequest("Eintrag ist nicht im Papierkorb")
    # Nur direkt gelöschte Einträge sind wiederherstellbar; das verhindert das
    # ungefragte Reaktivieren fremder, gelöschter Vorfahren.
    if not _is_top_level_trash(entry):
        raise BadRequest("Nur direkt gelöschte Einträge können wiederhergestellt werden")

    target = new_parent
    if target is None and entry.original_parent_id is not None:
        target = db.get(Entry, entry.original_parent_id)
    if target is None:
        target = db.get(Entry, entry.parent_id) if entry.parent_id else None
    if target is None:
        owner = entry.owner_id or actor_id
        target = get_root(db, owner) if owner is not None else None
    if target is None or target.type != EntryType.folder:
        raise NotFound("Zielverzeichnis nicht gefunden")
    if target.trashed_at is not None:
        raise BadRequest("Zielverzeichnis ist gelöscht")

    name = new_name or entry.name
    validate_name(name)
    sibling = find_child(db, target.id, name)
    if sibling is not None and sibling.id != entry.id:
        raise Conflict(f"'{name}' existiert im Ziel bereits")

    # Quota des betroffenen Besitzers erneut prüfen: Gelöschte Einträge zählen
    # nicht auf die Quota, sonst ließe sich der Papierkorb als Puffer nutzen und
    # die Quota durch wiederholtes Wiederherstellen dauerhaft überschreiten.
    _files, total = subtree_stats(db, entry)
    owner = entry.owner_id or actor_id
    if owner is not None:
        quota.enforce(db, owner, int(total))

    entry.parent_id = target.id
    entry.name = name
    _untrash_subtree(db, entry)
    events.emit(
        db, "entry.restored", {"entry_id": str(entry.id), "parent_id": str(target.id)}
    )
    return entry


def purge(db: Session, entry: Entry) -> int:
    if entry.trashed_at is None:
        raise BadRequest("Eintrag ist nicht im Papierkorb")
    files, _ = subtree_stats(db, entry)
    entry_id = entry.id
    db.delete(entry)
    db.flush()
    events.emit(db, "entry.purged", {"entry_id": str(entry_id), "files": files})
    return files


def _active_subtree_size(db: Session, entry: Entry) -> int:
    """Summe der aktiven Dateigrößen im Teilbaum (iterativ, Papierkorb ausgenommen)."""
    total = 0
    seen: set[UUID] = set()
    frontier: list[Entry] = [entry]
    while frontier:
        level: list[Entry] = []
        for node in frontier:
            if node.id in seen:
                continue
            seen.add(node.id)
            if node.type == EntryType.file:
                total += int(node.size or 0)
                continue
            level.append(node)
        if not level:
            break
        frontier = _children_of(db, [node.id for node in level], trashed=False)
    return total


def _clone_entry(
    db: Session, entry: Entry, new_parent: Entry, new_name: str, owner_id: UUID
) -> Entry:
    """Legt eine einzelne Kopie an – ohne Quota-Prüfung (siehe ``copy``)."""
    clone = Entry(
        name=new_name,
        type=entry.type,
        owner_id=owner_id,
        parent_id=new_parent.id,
        size=entry.size,
        mime=entry.mime,
        sha256=entry.sha256,
    )
    db.add(clone)
    db.flush()
    if entry.type == EntryType.file and entry.current_version_id is not None:
        source = db.get(Version, entry.current_version_id)
        if source is not None:
            clone_version = Version(
                entry_id=clone.id,
                blob_id=source.blob_id,
                seq=1,
                size=source.size,
                mime=source.mime,
                sha256=source.sha256,
                created_by=owner_id,
            )
            db.add(clone_version)
            db.flush()
            clone.current_version_id = clone_version.id
            db.flush()
    return clone


def _copy_subtree(
    db: Session, entry: Entry, new_parent: Entry, new_name: str, owner_id: UUID
) -> Entry:
    """Kopiert einen Eintrag samt aktivem Teilbaum – ohne Quota-Prüfung.

    Iterativ und ebenenweise (eine Child-Query je Ebene) statt rekursiv, damit
    auch sehr tiefe Bäume nicht in einen RecursionError laufen (analog zu
    ``_trash_subtree``). ``seen`` schützt vor Zyklen in inkonsistenten Daten.
    """
    root_clone = _clone_entry(db, entry, new_parent, new_name, owner_id)
    clone_by_source: dict[UUID, Entry] = {entry.id: root_clone}
    seen: set[UUID] = {entry.id}
    frontier: list[Entry] = [entry] if entry.type == EntryType.folder else []
    while frontier:
        children = _children_of(db, [node.id for node in frontier], trashed=False)
        next_frontier: list[Entry] = []
        for child in children:
            if child.id in seen:
                continue
            seen.add(child.id)
            parent_clone = clone_by_source.get(child.parent_id)
            if parent_clone is None:
                # Inkonsistente Daten: Kind ohne kopierten Elternknoten überspringen.
                continue
            child_clone = _clone_entry(db, child, parent_clone, child.name, owner_id)
            clone_by_source[child.id] = child_clone
            if child.type == EntryType.folder:
                next_frontier.append(child)
        frontier = next_frontier
    return root_clone


def copy(db: Session, entry: Entry, new_parent: Entry, new_name: str, owner_id: UUID) -> Entry:
    validate_name(new_name)
    if new_parent.type != EntryType.folder:
        raise BadRequest("Ziel ist kein Verzeichnis")
    if entry.type == EntryType.folder and (
        new_parent.id == entry.id or _is_descendant(new_parent, entry.id)
    ):
        raise BadRequest("Ein Ordner kann nicht in sich selbst kopiert werden")
    if find_child(db, new_parent.id, new_name) is not None:
        raise Conflict(f"'{new_name}' existiert bereits")
    # Kopien zählen auf die Quota des Kopierenden (Blob-Dedup hin oder her). Bei
    # Ordnern wird der aktive Teilbaum vorab summiert und genau einmal geprüft,
    # damit die einzelnen Kopien die Quota nicht mehrfach anrechnen.
    quota.enforce(db, owner_id, _active_subtree_size(db, entry))
    clone = _copy_subtree(db, entry, new_parent, new_name, owner_id)
    events.emit(db, "entry.copied", {"entry_id": str(clone.id)})
    return clone
