"""Papierkorb: auflisten, wiederherstellen, endgültig löschen."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core import audit, authz, namespace
from ..errors import NotFound, PermissionDenied
from ..models import Entry, User
from ..utils import as_utc, utcnow
from .deps import client_ip, get_current_user, get_db
from .entries import entries_by_ids, entry_paths, to_entry_out
from .schemas import (
    BulkIds,
    BulkPurgeResult,
    BulkResult,
    EntryOut,
    RestoreRequest,
    TrashCount,
    TrashEntryOut,
)

router = APIRouter(tags=["trash"])


def _to_trash_out(db: Session, entry: Entry, path: str | None = None) -> TrashEntryOut:
    files, size = namespace.subtree_stats(db, entry)
    retention = get_settings().trash_retention_days
    days_left = 0
    trashed_at = as_utc(entry.trashed_at)
    if trashed_at is not None:
        elapsed = (utcnow() - trashed_at).days
        days_left = max(retention - elapsed, 0)
    return TrashEntryOut(
        id=entry.id,
        name=entry.name,
        path=path if path is not None else namespace.path_of(db, entry),
        type=entry.type,
        size=size,
        files=files,
        trashed_at=trashed_at,
        trashed_by=entry.trashed_by,
        days_left=days_left,
    )


@router.get("/trash", response_model=list[TrashEntryOut])
def list_trash(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[TrashEntryOut]:
    entries = namespace.list_trash(db, user)
    # Eltern gebündelt laden und Pfade einmal je Vorfahrenkette statt je
    # Eintrag bestimmen (``_to_trash_out`` bekommt den Pfad übergeben).
    parents = entries_by_ids(
        db, [entry.parent_id for entry in entries if entry.parent_id is not None]
    )
    paths = entry_paths(db, entries, parents)
    return [_to_trash_out(db, entry, path=paths[entry.id]) for entry in entries]


@router.get("/trash/count", response_model=TrashCount)
def trash_count(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TrashCount:
    return TrashCount(count=namespace.trash_count(db, user))


@router.post("/trash/bulk/restore", response_model=BulkResult)
def bulk_restore_trash(
    payload: BulkIds,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BulkResult:
    """Stellt mehrere Papierkorb-Einträge wieder her (jeweils am Ursprungsort)."""
    ok = 0
    failed: list[UUID] = []
    for entry_id in payload.ids:
        try:
            with db.begin_nested():
                entry = namespace.get_trashed_entry(db, entry_id)
                if not namespace.is_top_level_trash(entry):
                    raise PermissionDenied(
                        "Nur direkt gelöschte Einträge können wiederhergestellt werden"
                    )
                if not namespace.can_manage_trash(db, user, entry):
                    raise PermissionDenied("Kein Recht, diesen Eintrag wiederherzustellen")
                # Wie beim Einzel-Wiederherstellen: `read` auf der Quelle verhindert
                # das Verlagern eines delete-only-Eintrags in einen lesbaren Ordner.
                authz.authorize(db, user, "read", entry)

                original = (
                    db.get(Entry, entry.original_parent_id)
                    if entry.original_parent_id
                    else None
                )
                if original is not None and original.trashed_at is None:
                    target = original
                else:
                    target = namespace.ensure_root(db, user.id)
                if target is None:
                    raise NotFound("Zielverzeichnis nicht gefunden")
                authz.authorize(db, user, "write", target)

                namespace.restore(db, entry, actor_id=user.id, new_parent=target)
            ok += 1
        except Exception:
            failed.append(entry_id)

    audit.record(
        db, "trash.bulk_restore", actor_id=user.id, protocol="http",
        ip=client_ip(request), details={"ok": ok, "failed": len(failed)},
    )
    return BulkResult(ok=ok, failed=failed)


@router.post("/trash/bulk/purge", response_model=BulkPurgeResult)
def bulk_purge_trash(
    payload: BulkIds,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BulkPurgeResult:
    """Löscht mehrere Papierkorb-Einträge endgültig."""
    ok = 0
    files = 0
    failed: list[UUID] = []
    for entry_id in payload.ids:
        try:
            with db.begin_nested():
                entry = namespace.get_trashed_entry(db, entry_id)
                if not namespace.can_manage_trash(db, user, entry):
                    raise PermissionDenied("Kein Recht, diesen Eintrag endgültig zu löschen")
                files += namespace.purge(db, entry)
            ok += 1
        except Exception:
            failed.append(entry_id)

    audit.record(
        db, "trash.bulk_purge", actor_id=user.id, protocol="http",
        ip=client_ip(request), details={"ok": ok, "failed": len(failed), "files": files},
    )
    return BulkPurgeResult(ok=ok, failed=failed, files=files)


@router.post("/trash/{entry_id}/restore", response_model=EntryOut)
def restore_entry(
    entry_id: UUID,
    payload: RestoreRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EntryOut:
    entry = namespace.get_trashed_entry(db, entry_id)
    # Nur direkt gelöschte (oberste) Einträge dürfen wiederhergestellt werden.
    if not namespace.is_top_level_trash(entry):
        raise PermissionDenied(
            "Nur direkt gelöschte Einträge können wiederhergestellt werden"
        )
    if not namespace.can_manage_trash(db, user, entry):
        raise PermissionDenied("Kein Recht, diesen Eintrag wiederherzustellen")
    # Wie beim Verschieben ändert das Wiederherstellen die Vererbungskette des
    # Eintrags. Ohne `read` auf der Quelle ließe sich ein delete-only-Eintrag in
    # einen lesbaren Zielordner holen und dort lesen (F2).
    authz.authorize(db, user, "read", entry)

    # Ziel bestimmen und Schreibrecht darauf verlangen (auch beim impliziten Ziel).
    if payload.parent_id is not None:
        target = namespace.get_entry(db, payload.parent_id)
    else:
        original = (
            db.get(Entry, entry.original_parent_id) if entry.original_parent_id else None
        )
        if original is not None and original.trashed_at is None:
            target = original
        else:
            target = namespace.ensure_root(db, user.id)
    if target is None:
        raise NotFound("Zielverzeichnis nicht gefunden")
    authz.authorize(db, user, "write", target)

    restored = namespace.restore(
        db, entry, actor_id=user.id, new_parent=target, new_name=payload.new_name
    )
    audit.record(
        db, "trash.restore", actor_id=user.id, target_entry=restored.id,
        protocol="http", ip=client_ip(request),
    )
    return to_entry_out(db, restored)


@router.delete("/trash/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def purge_entry(
    entry_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    entry = namespace.get_trashed_entry(db, entry_id)
    if not namespace.can_manage_trash(db, user, entry):
        raise PermissionDenied("Kein Recht, diesen Eintrag endgültig zu löschen")
    files = namespace.purge(db, entry)
    audit.record(
        db, "trash.purge", actor_id=user.id, target_entry=entry_id,
        protocol="http", ip=client_ip(request), details={"files": files},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/trash", status_code=status.HTTP_200_OK)
def empty_trash(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    entries = namespace.list_trash(db, user)
    removed = 0
    for entry in entries:
        removed += namespace.purge(db, entry)
    audit.record(
        db, "trash.empty", actor_id=user.id, protocol="http", ip=client_ip(request),
        details={"entries": len(entries), "files": removed},
    )
    return {"entries": len(entries), "files": removed}
