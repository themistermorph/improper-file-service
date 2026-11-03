"""Administrations-Endpunkte: Gruppen, ACLs, Audit (Benutzer siehe users.py)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import accounts, audit, authz, namespace, permissions
from ..models import ACL, Group, PrincipalType, User
from .deps import (
    client_ip,
    get_current_user,
    get_db,
    require_admin,
    require_audit,
)
from .schemas import (
    AclCreate,
    AclOut,
    AclUpsert,
    GroupCreate,
    GroupOut,
    MemberAdd,
    PrincipalOut,
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


def _acl_out(acl: ACL) -> AclOut:
    return AclOut(
        id=acl.id,
        entry_id=acl.entry_id,
        principal_type=acl.principal_type,
        principal_id=acl.principal_id,
        perms=acl.perms,
        permission_names=authz.names_from_perms(acl.perms, namespace="entry"),
        inherited=acl.inherited,
    )


def _validate_principal(
    db: Session, principal_type, principal_id: UUID
) -> None:
    if not accounts.principal_exists(db, principal_type, principal_id):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Principal (Benutzer/Gruppe) nicht gefunden"
        )


@router.get("/entries/{entry_id}/acl", response_model=list[AclOut])
def list_acl(
    entry_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[AclOut]:
    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "admin", entry)
    return [_acl_out(acl) for acl in permissions.list_entry_acls(db, entry.id)]


@router.post("/entries/{entry_id}/acl", response_model=AclOut, status_code=status.HTTP_201_CREATED)
def set_acl(
    entry_id: UUID,
    payload: AclCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AclOut:
    """Kompatibler POST: Bits werden mit einem bestehenden Grant vereinigt."""
    entry = namespace.get_entry(db, entry_id)
    # Wer `admin` auf dem Eintrag hat (Besitzer/Manager), darf Rechte vergeben.
    authz.authorize(db, user, "admin", entry)
    _validate_principal(db, payload.principal_type, payload.principal_id)
    perms = authz.perms_from_names(payload.perms, namespace="entry")
    acl = permissions.upsert_acl(
        db,
        entry_id=entry.id,
        principal_type=payload.principal_type,
        principal_id=payload.principal_id,
        perms=perms,
        merge=True,
    )
    audit.record(
        db, "acl.set", actor_id=user.id, target_entry=entry.id, protocol="http",
        ip=client_ip(request),
        details={"principal": str(payload.principal_id), "perms": payload.perms},
    )
    return _acl_out(acl)


@router.put("/entries/{entry_id}/acl", response_model=AclOut)
def put_acl(
    entry_id: UUID,
    payload: AclUpsert,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AclOut:
    """Setzt die Eintragsrechte eines Principals exakt (Upsert)."""
    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "admin", entry)
    _validate_principal(db, payload.principal_type, payload.principal_id)
    perms = authz.perms_from_names(payload.permissions, namespace="entry")
    acl = permissions.upsert_acl(
        db,
        entry_id=entry.id,
        principal_type=payload.principal_type,
        principal_id=payload.principal_id,
        perms=perms,
        merge=False,
    )
    audit.record(
        db, "acl.set", actor_id=user.id, target_entry=entry.id, protocol="http",
        ip=client_ip(request),
        details={
            "principal": str(payload.principal_id),
            "perms": payload.permissions,
        },
    )
    return _acl_out(acl)


@router.delete(
    "/entries/{entry_id}/acl/{acl_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_acl(
    entry_id: UUID,
    acl_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "admin", entry)
    acl = permissions.get_acl(db, acl_id)
    if acl.entry_id != entry.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ACL gehört nicht zu diesem Eintrag")
    principal_id = acl.principal_id
    permissions.delete_acl(db, acl)
    audit.record(
        db, "acl.delete", actor_id=user.id, target_entry=entry.id, protocol="http",
        ip=client_ip(request), details={"principal": str(principal_id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/principals", response_model=list[PrincipalOut])
def list_principals(
    entry_id: UUID,
    q: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[PrincipalOut]:
    """Minimierter Benutzer-/Gruppen-Picker für den Zugriff-Dialog.

    Autorisierung über `admin` am Eintrag – bewusst kein Systemadmin-Zwang.
    E-Mail-Adressen und Adminflags werden nicht ausgegeben.
    """
    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "admin", entry)
    bounded = min(max(limit, 1), 100)
    users, _total = accounts.list_users(
        db, q=q, active=True, limit=bounded, offset=0
    )
    result = [
        PrincipalOut(
            id=u.id,
            type=PrincipalType.user,
            name=u.username,
            display_name=u.display_name,
        )
        for u in users
    ]
    stmt = select(Group).order_by(Group.name).limit(bounded)
    if q:
        stmt = stmt.where(func.lower(Group.name).like(f"%{q.lower()}%"))
    result.extend(
        PrincipalOut(id=g.id, type=PrincipalType.group, name=g.name)
        for g in db.execute(stmt).scalars().all()
    )
    return result



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
