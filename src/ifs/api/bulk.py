"""Bulk-Aktionen auf mehreren Einträgen (Verschieben, Löschen)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import audit, authz, namespace
from ..errors import NotFound
from ..models import Entry, User
from .deps import client_ip, get_current_user, get_db
from .schemas import BulkIds, BulkMove, BulkResult

router = APIRouter(tags=["bulk"])

# Bind-Parameter je IN-Query begrenzen (SQLite alte Builds: max. 999).
_IN_CHUNK_SIZE = 500


def _load_entries(db: Session, ids: list[UUID]) -> dict[UUID, Entry]:
    """Lädt alle gewählten Einträge in einer Abfrage statt je Eintrag."""
    unique_ids = list(dict.fromkeys(ids))
    entries: dict[UUID, Entry] = {}
    for start in range(0, len(unique_ids), _IN_CHUNK_SIZE):
        chunk = unique_ids[start : start + _IN_CHUNK_SIZE]
        for entry in db.execute(select(Entry).where(Entry.id.in_(chunk))).scalars().all():
            entries[entry.id] = entry
    return entries


def _entry_or_fail(entries: dict[UUID, Entry], entry_id: UUID) -> Entry:
    """Wie ``namespace.get_entry``: fehlend oder im Papierkorb -> NotFound."""
    entry = entries.get(entry_id)
    if entry is None or entry.trashed_at is not None:
        raise NotFound("Eintrag nicht gefunden")
    return entry


@router.post("/bulk/move", response_model=BulkResult)
def bulk_move(
    payload: BulkMove,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BulkResult:
    target = namespace.get_entry(db, payload.parent_id)
    authz.authorize(db, user, "write", target)

    known = _load_entries(db, payload.ids)
    ok = 0
    failed = []
    for entry_id in payload.ids:
        try:
            with db.begin_nested():
                entry = _entry_or_fail(known, entry_id)
                # `read` auf der Quelle verhindert das Verschieben von
                # write-only-Einträgen in lesbare Ordner (Rechteausweitung).
                authz.authorize(db, user, "read", entry)
                authz.authorize(db, user, "write", entry)
                namespace.move(db, entry, target)
            ok += 1
        except Exception:
            failed.append(entry_id)

    audit.record(
        db, "bulk.move", actor_id=user.id, target_entry=target.id, protocol="http",
        ip=client_ip(request), details={"ok": ok, "failed": len(failed)},
    )
    return BulkResult(ok=ok, failed=failed)


@router.post("/bulk/delete", response_model=BulkResult)
def bulk_delete(
    payload: BulkIds,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BulkResult:
    known = _load_entries(db, payload.ids)
    ok = 0
    failed = []
    for entry_id in payload.ids:
        try:
            with db.begin_nested():
                entry = _entry_or_fail(known, entry_id)
                authz.authorize(db, user, "delete", entry)
                namespace.soft_delete(db, entry, user.id)
            ok += 1
        except Exception:
            failed.append(entry_id)

    audit.record(
        db, "bulk.delete", actor_id=user.id, protocol="http", ip=client_ip(request),
        details={"ok": ok, "failed": len(failed)},
    )
    return BulkResult(ok=ok, failed=failed)
