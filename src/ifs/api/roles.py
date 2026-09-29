"""Rollen: Definitionen, Zuweisungen, Eintrags- und Systemrollen."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from ..core import audit, authz, namespace, roles
from ..errors import PermissionDenied
from ..models import Entry, PrincipalType, Role, RoleAssignment, RoleScope, User
from .deps import client_ip, get_current_user, get_db, require_admin
from .schemas import (
    EntryRoleAssign,
    RoleAssignmentCreate,
    RoleAssignmentOut,
    RoleCreate,
    RoleOut,
    RoleUpdate,
)

router = APIRouter(tags=["roles"])


def _role_out(role: Role) -> RoleOut:
    return RoleOut(
        id=role.id,
        name=role.name,
        description=role.description,
        permissions=role.permissions,
        permission_names=authz.names_from_perms(role.permissions),
        scope=role.scope,
        is_builtin=role.is_builtin,
    )


def _assignment_out(assignment: RoleAssignment) -> RoleAssignmentOut:
    role = assignment.role
    return RoleAssignmentOut(
        id=assignment.id,
        role_id=assignment.role_id,
        role_name=role.name if role else None,
        scope=role.scope if role else None,
        principal_type=assignment.principal_type,
        principal_id=assignment.principal_id,
        entry_id=assignment.entry_id,
        created_at=assignment.created_at,
    )


# --- Rollendefinitionen (globaler Admin) ----------------------------------


@router.get("/roles", response_model=list[RoleOut])
def list_roles(
    scope: RoleScope | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[RoleOut]:
    return [_role_out(role) for role in roles.list_roles(db, scope=scope)]


@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> RoleOut:
    role = roles.create_role(
        db,
        name=payload.name,
        scope=payload.scope,
        permissions=authz.perms_from_names(payload.permissions),
        description=payload.description,
    )
    audit.record(
        db, "role.create", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={"role": role.name, "scope": role.scope.value},
    )
    return _role_out(role)


@router.get("/roles/{role_id}", response_model=RoleOut)
def get_role(
    role_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> RoleOut:
    return _role_out(roles.get_role(db, role_id))


@router.patch("/roles/{role_id}", response_model=RoleOut)
def update_role(
    role_id: UUID,
    payload: RoleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> RoleOut:
    role = roles.get_role(db, role_id)
    permissions = (
        authz.perms_from_names(payload.permissions)
        if payload.permissions is not None
        else None
    )
    roles.update_role(db, role, description=payload.description, permissions=permissions)
    audit.record(
        db, "role.update", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={"role": role.name},
    )
    return _role_out(role)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> Response:
    role = roles.get_role(db, role_id)
    name = role.name
    roles.delete_role(db, role)
    audit.record(
        db, "role.delete", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={"role": name},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Zuweisungen allgemein (globaler Admin) -------------------------------


@router.get("/roles/{role_id}/assignments", response_model=list[RoleAssignmentOut])
def list_role_assignments(
    role_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[RoleAssignmentOut]:
    roles.get_role(db, role_id)
    return [_assignment_out(a) for a in roles.list_assignments(db, role_id=role_id)]


@router.post(
    "/roles/{role_id}/assignments",
    response_model=RoleAssignmentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_assignment(
    role_id: UUID,
    payload: RoleAssignmentCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> RoleAssignmentOut:
    role = roles.get_role(db, role_id)
    if payload.entry_id is not None:
        # Ressourcenrollen dürfen nur von Eintrags-Admins vergeben werden: Sonst
        # könnte sich ein Systemadmin per API Zugriff auf fremde Wurzelbäume
        # verschaffen (Doku §5: Admins haben keinen Dateizugriff).
        entry = namespace.get_entry(db, payload.entry_id)
        authz.authorize(db, actor, "admin", entry)
    assignment = roles.assign_role(
        db,
        role=role,
        principal_type=payload.principal_type,
        principal_id=payload.principal_id,
        entry_id=payload.entry_id,
    )
    audit.record(
        db, "role.assign", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={"role": role.name, "principal": str(payload.principal_id)},
    )
    return _assignment_out(assignment)


@router.delete("/roles/assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_assignment(
    assignment_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> Response:
    assignment = roles.get_assignment(db, assignment_id)
    if assignment.entry_id is not None:
        entry = db.get(Entry, assignment.entry_id)
        if entry is not None:
            # Entry-gebundene Zuweisungen darf nur entfernen, wer `admin` am
            # Eintrag hat – sonst könnte ein Systemadmin fremde Bäume ändern
            # (Doku §5/§17: Systemadmins haben keinen Dateizugriff).
            authz.authorize(db, actor, "admin", entry)
        # Ist der Eintrag bereits gelöscht (verwaiste Zuweisung), darf der
        # Systemadmin sie aufräumen; die Autorisierung ist dann nicht mehr möglich.
    role_name = assignment.role.name if assignment.role else None
    roles.remove_assignment(db, assignment)
    audit.record(
        db, "role.unassign", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={"role": role_name, "principal": str(assignment.principal_id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Eintragsrollen (Recht `admin` am Eintrag) ----------------------------


@router.get("/entries/{entry_id}/roles", response_model=list[RoleAssignmentOut])
def list_entry_roles(
    entry_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[RoleAssignmentOut]:
    from ..core import namespace

    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "admin", entry)
    return [_assignment_out(a) for a in roles.list_assignments(db, entry_id=entry_id)]


@router.post(
    "/entries/{entry_id}/roles",
    response_model=RoleAssignmentOut,
    status_code=status.HTTP_201_CREATED,
)
def assign_entry_role(
    entry_id: UUID,
    payload: EntryRoleAssign,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RoleAssignmentOut:
    from ..core import namespace

    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "admin", entry)
    role = roles.get_role(db, payload.role_id)
    assignment = roles.assign_role(
        db,
        role=role,
        principal_type=payload.principal_type,
        principal_id=payload.principal_id,
        entry_id=entry.id,
    )
    audit.record(
        db, "role.assign", actor_id=user.id, target_entry=entry.id, protocol="http",
        ip=client_ip(request), details={"role": role.name, "principal": str(payload.principal_id)},
    )
    return _assignment_out(assignment)


@router.delete(
    "/entries/{entry_id}/roles/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_entry_role(
    entry_id: UUID,
    assignment_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    from ..core import namespace

    entry = namespace.get_entry(db, entry_id)
    authz.authorize(db, user, "admin", entry)
    assignment = roles.get_assignment(db, assignment_id)
    if assignment.entry_id != entry.id:
        raise PermissionDenied("Zuweisung gehört nicht zu diesem Eintrag")
    roles.remove_assignment(db, assignment)
    audit.record(
        db, "role.unassign", actor_id=user.id, target_entry=entry.id, protocol="http",
        ip=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Systemrollen je Benutzer (globaler Admin) ----------------------------


@router.get("/users/{user_id}/roles", response_model=list[RoleAssignmentOut])
def list_user_roles(
    user_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[RoleAssignmentOut]:
    assignments = [
        a
        for a in roles.list_assignments(db, principal_id=user_id)
        if a.entry_id is None and a.principal_type == PrincipalType.user and a.role
        and a.role.scope == RoleScope.system
    ]
    return [_assignment_out(a) for a in assignments]


@router.post(
    "/users/{user_id}/roles/{role_id}", status_code=status.HTTP_201_CREATED
)
def assign_user_role(
    user_id: UUID,
    role_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> RoleAssignmentOut:
    from ..core import accounts

    accounts.get_user(db, user_id)
    role = roles.get_role(db, role_id)
    assignment = roles.assign_role(
        db,
        role=role,
        principal_type=PrincipalType.user,
        principal_id=user_id,
    )
    audit.record(
        db, "role.assign_system", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={"role": role.name, "user_id": str(user_id)},
    )
    return _assignment_out(assignment)


@router.delete(
    "/users/{user_id}/roles/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_user_role(
    user_id: UUID,
    assignment_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> Response:
    assignment = roles.get_assignment(db, assignment_id)
    if assignment.principal_id != user_id or assignment.entry_id is not None:
        raise PermissionDenied("Zuweisung gehört nicht zu diesem Benutzer")
    roles.remove_assignment(db, assignment)
    audit.record(
        db, "role.unassign_system", actor_id=actor.id, protocol="http", ip=client_ip(request),
        details={"user_id": str(user_id)},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
