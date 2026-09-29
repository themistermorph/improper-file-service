"""Namespace- und Transfer-Endpunkte (Verzeichnisse, Dateien, Download)."""

from __future__ import annotations

from email.utils import format_datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core import audit, authz, content, namespace
from ..models import (
    ACL,
    Blob,
    Entry,
    EntryType,
    PrincipalType,
    RoleAssignment,
    User,
    Version,
)
from ..utils import as_utc, content_disposition, safe_mime
from .deps import client_ip, get_current_user, get_db
from .schemas import (
    CopyRequest,
    EntryOut,
    EntryUpdate,
    FolderCreate,
    MoveRequest,
)

router = APIRouter(tags=["entries"])


def to_entry_out(db: Session, entry: Entry) -> EntryOut:
    return EntryOut(
        id=entry.id,
        parent_id=entry.parent_id,
        name=entry.name,
        type=entry.type,
        path=namespace.path_of(db, entry),
        size=entry.size,
        mime=entry.mime,
        sha256=entry.sha256,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


def _root_for(db: Session, user: User) -> Entry:
    """Wurzel (Home) des Benutzers; wird bei Bedarf angelegt."""
    return namespace.ensure_root(db, user.id)


@router.get("/entries", response_model=list[EntryOut])
def list_entries(
    parent_id: UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[EntryOut]:
    parent = namespace.get_entry(db, parent_id) if parent_id else _root_for(db, user)
    authz.authorize(db, user, "read", parent)
    return [to_entry_out(db, child) for child in namespace.list_children(db, parent)]


@router.get("/folders", response_model=list[EntryOut])
def list_folders(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[EntryOut]:
    """Alle lesbaren Ordner (für Auswahl-Dialoge wie „Verschieben“)."""
    folders = db.execute(
        select(Entry).where(Entry.type == EntryType.folder, Entry.trashed_at.is_(None))
    ).scalars().all()
    visible = [folder for folder in folders if authz.can(db, user, "read", folder)]
    visible.sort(key=lambda folder: namespace.path_of(db, folder))
    return [to_entry_out(db, folder) for folder in visible]


@router.get("/shared", response_model=list[EntryOut])
def list_shared(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[EntryOut]:
    """Einträge, die dem Benutzer freigegeben sind (ACL/Ressourcenrolle).

    Geliefert werden nur die obersten Einstiegspunkte (deren Parent für den
    Benutzer nicht lesbar ist) und keine eigenen Einträge.
    """
    group_ids = [group.id for group in user.groups]

    def principals(model):
        conditions = [
            (model.principal_type == PrincipalType.user) & (model.principal_id == user.id)
        ]
        if group_ids:
            conditions.append(
                (model.principal_type == PrincipalType.group)
                & (model.principal_id.in_(group_ids))
            )
        return or_(*conditions)

    candidate_ids: set[UUID] = set(
        db.execute(select(ACL.entry_id).where(principals(ACL))).scalars().all()
    )
    candidate_ids.update(
        db.execute(
            select(RoleAssignment.entry_id).where(
                RoleAssignment.entry_id.isnot(None), principals(RoleAssignment)
            )
        ).scalars().all()
    )

    entries: list[Entry] = []
    for entry_id in candidate_ids:
        entry = db.get(Entry, entry_id)
        if entry is None or entry.trashed_at is not None:
            continue
        if entry.owner_id == user.id:
            continue
        if not authz.can(db, user, "read", entry):
            continue
        parent = entry.parent
        if parent is not None and authz.can(db, user, "read", parent):
            continue  # kein oberster Einstiegspunkt
        entries.append(entry)
    entries.sort(key=lambda e: namespace.path_of(db, e).lower())
    return [to_entry_out(db, entry) for entry in entries]


@router.get("/root", response_model=EntryOut)
def get_root(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EntryOut:
    """Liefert die eigene Wurzel (nützlich für die Web-UI)."""
    root = _root_for(db, user)
    authz.authorize(db, user, "read", root)
    return to_entry_out(db, root)


@router.get("/entries/{entry_id}", response_model=EntryOut)
def get_entry(
    entry_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EntryOut:
    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "read", entry)
    return to_entry_out(db, entry)


@router.post("/folders", response_model=EntryOut, status_code=status.HTTP_201_CREATED)
def create_folder(
    payload: FolderCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EntryOut:
    parent = namespace.get_entry(db, payload.parent_id) if payload.parent_id else _root_for(db, user)
    authz.authorize(db, user, "write", parent)
    folder = namespace.create_folder(db, parent, payload.name, user.id)
    audit.record(
        db, "folder.create", actor_id=user.id, target_entry=folder.id,
        protocol="http", ip=client_ip(request),
    )
    return to_entry_out(db, folder)


@router.patch("/entries/{entry_id}", response_model=EntryOut)
def update_entry(
    entry_id: UUID,
    payload: EntryUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EntryOut:
    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "write", entry)
    namespace.rename(db, entry, payload.name)
    audit.record(
        db, "entry.rename", actor_id=user.id, target_entry=entry.id,
        protocol="http", ip=client_ip(request),
    )
    return to_entry_out(db, entry)


@router.post("/entries/{entry_id}/move", response_model=EntryOut)
def move_entry(
    entry_id: UUID,
    payload: MoveRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EntryOut:
    entry = namespace.get_entry(db, entry_id)
    target = namespace.get_entry(db, payload.parent_id)
    # Neben `write` auf Quelle und Ziel ist `read` auf der Quelle erforderlich:
    # `move` ändert die Vererbungskette, ein write-only-Eintrag ließe sich sonst
    # in einen lesbaren Ordner verschieben und danach lesen.
    authz.authorize(db, user, "read", entry)
    authz.authorize(db, user, "write", entry)
    authz.authorize(db, user, "write", target)
    namespace.move(db, entry, target)
    audit.record(
        db, "entry.move", actor_id=user.id, target_entry=entry.id,
        protocol="http", ip=client_ip(request), details={"parent_id": str(target.id)},
    )
    return to_entry_out(db, entry)


@router.post("/entries/{entry_id}/copy", response_model=EntryOut, status_code=status.HTTP_201_CREATED)
def copy_entry(
    entry_id: UUID,
    payload: CopyRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EntryOut:
    entry = namespace.get_entry(db, entry_id)
    target = namespace.get_entry(db, payload.parent_id)
    authz.authorize(db, user, "read", entry)
    # Eine Kopie wird Eigentum des Kopierenden und ist damit unabhängig
    # weiterverteilbar (Freigabelinks, ZIP-Export). Daher ist neben `read` auf der
    # Quelle auch das `share`-Recht erforderlich – sonst ließe sich das Verbot,
    # Links anzulegen, durch Kopieren umgehen.
    authz.authorize(db, user, "share", entry)
    authz.authorize(db, user, "write", target)
    clone = namespace.copy(db, entry, target, payload.name, user.id)
    audit.record(
        db, "entry.copy", actor_id=user.id, target_entry=clone.id,
        protocol="http", ip=client_ip(request),
    )
    return to_entry_out(db, clone)


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry(
    entry_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "delete", entry)
    namespace.soft_delete(db, entry, user.id)
    audit.record(
        db, "entry.delete", actor_id=user.id, target_entry=entry.id,
        protocol="http", ip=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    if not header:
        return None
    header = header.strip()
    if not header.startswith("bytes="):
        return None
    spec = header[len("bytes="):].split(",")[0].strip()
    if "-" not in spec:
        raise HTTPException(status.HTTP_416_RANGE_NOT_SATISFIABLE, "Ungültiger Range")
    start_str, end_str = spec.split("-", 1)
    try:
        if start_str == "":
            if end_str == "" or size == 0:
                raise HTTPException(416, "Ungültiger Suffix-Range")
            suffix = int(end_str)
            start, end = max(size - suffix, 0), size - 1
        else:
            start = int(start_str)
            end = int(end_str) if end_str else size - 1
    except ValueError:
        raise HTTPException(
            status.HTTP_416_RANGE_NOT_SATISFIABLE, "Ungültiger Range"
        )
    if size == 0 or start >= size or start > end:
        raise HTTPException(
            status.HTTP_416_RANGE_NOT_SATISFIABLE,
            "Range außerhalb der Datei",
            headers={"Content-Range": f"bytes */{size}"},
        )
    return start, min(end, size - 1)


def resolve_blob(db: Session, entry: Entry) -> Blob:
    if entry.type != EntryType.file or entry.current_version_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Eintrag hat keinen Dateiinhalt")
    version = db.get(Version, entry.current_version_id)
    blob = db.get(Blob, version.blob_id) if version else None
    if blob is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Blob fehlt")
    return blob


# Aktive Typen, die nicht inline (im App-Origin) ausgeliefert werden dürfen.
INLINE_RISKY_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
    "text/xml",
    "application/xml",
    "application/xslt+xml",
}


def stream_entry_content(
    db: Session,
    entry: Entry,
    request: Request,
    *,
    download: bool = False,
    actor_id: UUID | None = None,
    inline_active: bool = False,
) -> Response:
    """Streamt den aktuellen Inhalt eines Eintrags. Autorisierung obliegt dem Aufrufer."""
    return stream_blob(
        db,
        entry,
        resolve_blob(db, entry),
        request,
        download=download,
        actor_id=actor_id,
        inline_active=inline_active,
    )


def stream_blob(
    db: Session,
    entry: Entry,
    blob: Blob,
    request: Request,
    *,
    download: bool = False,
    actor_id: UUID | None = None,
    inline_active: bool = False,
) -> Response:
    """Streamt einen bestimmten Blob (mit Range-Support). Autorisierung obliegt dem Aufrufer."""
    content_type = safe_mime(entry.mime)
    # Aktive Inhalte immer als Download ausliefern (kein Inline-XSS im App-Origin),
    # außer der Aufrufer sandboxt sie selbst (Vorschau-Endpunkt). Der Vergleich ist
    # bewusst case-insensitiv (safe_mime normalisiert bereits).
    force_attachment = not inline_active and content_type.lower() in INLINE_RISKY_TYPES
    base_headers = {
        "ETag": f'"{blob.sha256}"',
        "Accept-Ranges": "bytes",
        "Last-Modified": format_datetime(as_utc(entry.updated_at)),
        "Content-Type": content_type,
        "X-Content-Type-Options": "nosniff",
    }
    if download or force_attachment:
        base_headers["Content-Disposition"] = content_disposition(entry.name)
    if force_attachment:
        base_headers["Content-Security-Policy"] = "sandbox; default-src 'none'"

    byte_range = _parse_range(request.headers.get("range"), blob.size)

    if request.method == "HEAD":
        base_headers["Content-Length"] = str(blob.size)
        return Response(status_code=status.HTTP_200_OK, headers=base_headers)

    start, end = byte_range if byte_range else (None, None)
    response = content.get_object(blob.storage_key, start, end)
    body = response["Body"]

    if byte_range:
        base_headers["Content-Range"] = f"bytes {start}-{end}/{blob.size}"
        base_headers["Content-Length"] = str(response["ContentLength"])
    else:
        base_headers["Content-Length"] = str(blob.size)

    audit.record(
        db, "transfer.download", actor_id=actor_id, target_entry=entry.id,
        protocol="http", ip=client_ip(request),
        details={"range": request.headers.get("range"), "size": blob.size},
    )
    return StreamingResponse(
        content.stream_out(body),
        status_code=status.HTTP_206_PARTIAL_CONTENT if byte_range else status.HTTP_200_OK,
        headers=base_headers,
    )


def _serve_content(
    entry_id: UUID,
    request: Request,
    download: bool,
    db: Session,
    user: User,
) -> Response:
    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "read", entry)
    return stream_entry_content(db, entry, request, download=download, actor_id=user.id)


@router.get("/entries/{entry_id}/content")
def download(
    entry_id: UUID,
    request: Request,
    download: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    return _serve_content(entry_id, request, download, db, user)


@router.head("/entries/{entry_id}/content", include_in_schema=False)
def download_head(
    entry_id: UUID,
    request: Request,
    download: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    return _serve_content(entry_id, request, download, db, user)
