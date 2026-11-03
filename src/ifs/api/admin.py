"""Administrations-Endpunkte: Gruppen, ACLs, Audit (Benutzer siehe users.py)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import audit, authz, permissions
from ..models import Group, PrincipalType, User
from .deps import (
    client_ip,
    get_db,
    require_admin,
    require_audit,
)
from .schemas import (
    GroupCreate,
    GroupOut,
    MemberAdd,
    SystemPermissionOut,
    SystemPermissionUpdate,
)

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


@router.get("/audit")
def list_audit(
    limit: int = 100,
    offset: int = 0,
    action: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_audit),
) -> list[dict]:
    rows = audit.list_recent(db, limit=limit, offset=offset, action=action)
    return [
        {
            "id": str(row.id),
            "ts": row.ts.isoformat(),
            "actor_id": str(row.actor_id) if row.actor_id else None,
            "actor_username": username,
            "actor_display_name": display_name,
            "action": row.action,
            "target_entry": str(row.target_entry) if row.target_entry else None,
            "protocol": row.protocol,
            "ip": row.ip,
            "result": row.result,
            "details": row.details,
        }
        for row, username, display_name in rows
    ]


# --- Systemberechtigungen (audit/monitor) ---------------------------------


def _system_permission_out(
    principal_type: PrincipalType, principal_id: UUID, row
) -> SystemPermissionOut:
    perms = int(row.permissions or 0) if row is not None else 0
    return SystemPermissionOut(
        principal_type=principal_type,
        principal_id=principal_id,
        permissions=perms,
        permission_names=authz.names_from_perms(perms, namespace="system"),
    )


def _parse_system_permissions(names: list[str]) -> int:
    try:
        perms = authz.perms_from_names(names, namespace="system")
        authz.validate_system_grant_perms(perms)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return perms


@router.get("/groups/{group_id}/permissions", response_model=SystemPermissionOut)
def get_group_permissions(
    group_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> SystemPermissionOut:
    group = _get_or_404(db, Group, group_id, "Gruppe")
    row = permissions.get_system_permission(db, PrincipalType.group, group.id)
    return _system_permission_out(PrincipalType.group, group.id, row)


@router.put("/groups/{group_id}/permissions", response_model=SystemPermissionOut)
def set_group_permissions(
    group_id: UUID,
    payload: SystemPermissionUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> SystemPermissionOut:
    group = _get_or_404(db, Group, group_id, "Gruppe")
    perms = _parse_system_permissions(payload.permissions)
    row = permissions.set_system_permissions(
        db,
        principal_type=PrincipalType.group,
        principal_id=group.id,
        perms=perms,
    )
    audit.record(
        db, "system_permission.set", actor_id=actor.id, protocol="http",
        ip=client_ip(request),
        details={
            "principal_type": "group",
            "principal_id": str(group.id),
            "permissions": payload.permissions,
        },
    )
    return _system_permission_out(PrincipalType.group, group.id, row)
