"""Administrations-Endpunkte: Gruppen, ACLs, Audit (Benutzer siehe users.py)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import accounts, audit, authz, namespace
from ..models import ACL, Group, User
from .deps import client_ip, get_current_user, get_db, require_admin
from .schemas import AclCreate, AclOut, GroupCreate, GroupOut, MemberAdd

router = APIRouter(tags=["admin"])


def _get_or_404(db: Session, model, obj_id: UUID, label: str):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label} nicht gefunden")
    return obj


@router.get("/groups", response_model=list[GroupOut])
def list_groups(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.execute(select(Group).order_by(Group.name)).scalars().all()


@router.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_group(
    payload: GroupCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)
) -> Group:
    if db.execute(select(Group).where(Group.name == payload.name)).scalars().first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Gruppe existiert bereits")
    group = Group(name=payload.name)
    db.add(group)
    db.flush()
    return group


@router.post("/groups/{group_id}/members", status_code=status.HTTP_204_NO_CONTENT)
def add_member(
    group_id: UUID,
    payload: MemberAdd,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> Response:
    group = _get_or_404(db, Group, group_id, "Gruppe")
    user = _get_or_404(db, User, payload.user_id, "Benutzer")
    if user not in group.members:
        group.members.append(user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/entries/{entry_id}/acl", response_model=AclOut, status_code=status.HTTP_201_CREATED)
def set_acl(
    entry_id: UUID,
    payload: AclCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ACL:
    entry = namespace.get_entry(db, entry_id)
    # Wer `admin` auf dem Eintrag hat (Besitzer/Manager), darf Rechte vergeben.
    authz.authorize(db, user, "admin", entry)
    if not accounts.principal_exists(db, payload.principal_type, payload.principal_id):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Principal (Benutzer/Gruppe) nicht gefunden"
        )
    perms = authz.perms_from_names(payload.perms)
    acl = ACL(
        entry_id=entry.id,
        principal_type=payload.principal_type,
        principal_id=payload.principal_id,
        perms=perms,
        inherited=False,
    )
    db.add(acl)
    db.flush()
    audit.record(
        db, "acl.set", actor_id=user.id, target_entry=entry.id, protocol="http",
        ip=client_ip(request),
        details={"principal": str(payload.principal_id), "perms": payload.perms},
    )
    return acl


@router.get("/audit")
def list_audit(
    limit: int = 100,
    offset: int = 0,
    action: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[dict]:
    rows = audit.list_recent(db, limit=limit, offset=offset, action=action)
    return [
        {
            "id": str(row.id),
            "ts": row.ts.isoformat(),
            "actor_id": str(row.actor_id) if row.actor_id else None,
            "actor_username": username,
            "action": row.action,
            "target_entry": str(row.target_entry) if row.target_entry else None,
            "protocol": row.protocol,
            "ip": row.ip,
            "result": row.result,
            "details": row.details,
        }
        for row, username in rows
    ]
