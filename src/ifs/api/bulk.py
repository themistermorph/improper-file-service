"""Bulk-Aktionen auf mehreren Einträgen (Verschieben, Löschen)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..core import audit, authz, namespace
from ..models import User
from .deps import client_ip, get_current_user, get_db
from .schemas import BulkIds, BulkMove, BulkResult

router = APIRouter(tags=["bulk"])


@router.post("/bulk/move", response_model=BulkResult)
def bulk_move(
    payload: BulkMove,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BulkResult:
    target = namespace.get_entry(db, payload.parent_id)
    authz.authorize(db, user, "write", target)

    ok = 0
    failed = []
    for entry_id in payload.ids:
        try:
            with db.begin_nested():
                entry = namespace.get_entry(db, entry_id)
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
    ok = 0
    failed = []
    for entry_id in payload.ids:
        try:
            with db.begin_nested():
                entry = namespace.get_entry(db, entry_id)
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
