"""Namespace: Verzeichnisbaum, Dateien, Versionen, Rename/Move, Papierkorb."""

from __future__ import annotations

import mimetypes
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import BadRequest, Conflict, NotFound
from ..models import Blob, Entry, EntryType, Share, User, Version
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


def path_of(db: Session, entry: Entry) -> str:
    parts: list[str] = []
    node: Entry | None = entry
    seen: set[UUID] = set()
    while node is not None and node.parent_id is not None:
        if node.id in seen:
            # Defense-in-Depth: Ein Zyklus im Baum (durch die Zeilensperre in
            # ``move`` verhindert) darf hier nicht zur Endlosschleife führen.
            break
        seen.add(node.id)
        parts.append(node.name)
        node = node.parent
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
        select(Entry).where(Entry.parent_id == parent.id, Entry.trashed_at.is_(None))
    ).scalars().all()
    entries.sort(key=lambda e: (e.type != EntryType.folder, e.name.lower()))
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


def ensure_writable(
    db: Session, parent: Entry, name: str, owner_id: UUID, size: int
) -> None:
    """Vorabprüfung **vor** dem S3-Upload: Name, Zieltyp, Konflikt und Quota.

    Verhindert verwaiste Objekte im Objektspeicher, wenn ein Upload sonst erst
    nach dem Hochladen an einem absehbaren Fehler (Namenskonflikt, Quota)
    scheitern würde. Die eigentliche Prüfung erfolgt in ``apply_write`` erneut.
    """
    validate_name(name)
    if parent.type != EntryType.folder:
        raise BadRequest("Ziel ist kein Verzeichnis")
    existing = find_child(db, parent.id, name)
    if existing is not None and existing.type == EntryType.folder:
        raise Conflict(f"'{name}' ist ein Verzeichnis")
    existing_size = existing.size if existing is not None else 0
    quota.enforce(db, owner_id, int(size) - existing_size)


def apply_write(
    db: Session,
    parent: Entry,
    name: str,
    owner_id: UUID,
    blob: Blob,
    mime: str | None = None,
) -> Entry:
    """Legt eine Datei an oder ersetzt ihren Inhalt durch eine neue Version."""
    ensure_writable(db, parent, name, owner_id, blob.size)
    if mime is None:
        mime = mimetypes.guess_type(name)[0]
    mime = safe_mime(mime)

    existing = find_child(db, parent.id, name)

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

    # Freigabelinks auf gelöschte Einträge automatisch widerrufen.
    revoked = 0
    if trashed_ids:
        result = db.execute(delete(Share).where(Share.entry_id.in_(trashed_ids)))
        revoked = result.rowcount or 0
        db.flush()

    events.emit(
        db,
        "entry.trashed",
        {"entry_id": str(entry.id), "by": str(actor_id) if actor_id else None, "revoked_shares": revoked},
    )


def _trash_subtree(db: Session, entry: Entry, actor_id: UUID | None, ids: list[UUID]) -> None:
    # Iterativ statt rekursiv, damit auch sehr tiefe Bäume nicht in einen
    # RecursionError laufen.
    stack: list[Entry] = [entry]
    while stack:
        node = stack.pop()
        node.trashed_at = utcnow()
        node.trashed_by = actor_id
        if node.original_parent_id is None:
            node.original_parent_id = node.parent_id
        node.updated_at = utcnow()
        ids.append(node.id)
        stack.extend(
            db.execute(
                select(Entry).where(Entry.parent_id == node.id, Entry.trashed_at.is_(None))
            ).scalars().all()
        )
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


def list_trash(db: Session, user: User) -> list[Entry]:
    rows = db.execute(select(Entry).where(Entry.trashed_at.isnot(None))).scalars().all()
    top = [e for e in rows if _is_top_level_trash(e) and can_manage_trash(db, user, e)]
    top.sort(key=lambda e: e.trashed_at or e.updated_at, reverse=True)
    return top


def trash_count(db: Session, user: User) -> int:
    return len(list_trash(db, user))


def subtree_stats(db: Session, entry: Entry) -> tuple[int, int]:
    """Liefert (Dateien, Bytes) im Teilbaum unter dem Eintrag."""
    files = 0
    total = 0
    stack = [entry]
    seen: set = set()
    while stack:
        node = stack.pop()
        if node.id in seen:
            continue
        seen.add(node.id)
        if node.type == EntryType.file:
            files += 1
            total += node.size
        children = db.execute(
            select(Entry).where(Entry.parent_id == node.id)
        ).scalars().all()
        stack.extend(children)
    return files, total


def _untrash_node(node: Entry) -> None:
    node.trashed_at = None
    node.trashed_by = None
    node.original_parent_id = None
    node.updated_at = utcnow()


def _untrash_subtree(db: Session, entry: Entry) -> None:
    # Iterativ statt rekursiv (siehe _trash_subtree).
    stack: list[Entry] = [entry]
    while stack:
        node = stack.pop()
        _untrash_node(node)
        stack.extend(
            db.execute(
                select(Entry).where(Entry.parent_id == node.id, Entry.trashed_at.isnot(None))
            ).scalars().all()
        )
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
    # Kopien zählen auf die Quota des Kopierenden (Blob-Dedup hin oder her).
    quota.enforce(db, owner_id, int(entry.size or 0))
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
    events.emit(db, "entry.copied", {"entry_id": str(clone.id)})
    return clone
