"""Rollen: Definitionen, Zuweisungen, Eintrags- und Systemrollen."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from ..core import authz, roles
from ..models import Role, RoleAssignment, RoleScope, User
from .deps import get_current_user, get_db, require_admin
from .schemas import (
    EntryRoleAssign,
    RoleAssignmentCreate,
    RoleAssignmentOut,
    RoleCreate,
    RoleOut,
    RoleUpdate,
)

router = APIRouter(tags=["roles"])

_ROLES_REMOVED = (
    "Fachliche Rollen wurden durch Berechtigungen ersetzt. "
    "Eintragsrechte werden über /api/entries/{id}/acl, Systemrechte über "
    "/api/users/{id}/permissions bzw. /api/groups/{id}/permissions vergeben."
)


def _gone() -> None:
    raise HTTPException(status.HTTP_410_GONE, _ROLES_REMOVED)


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
    return [
        _role_out(role)
        for role in roles.list_roles(db, scope=scope)
        if role.name == "admin"
    ]


@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> RoleOut:
    _gone()


@router.get("/roles/{role_id}", response_model=RoleOut)
def get_role(
    role_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> RoleOut:
    role = roles.get_role(db, role_id)
    if role.name != "admin":
        _gone()
    return _role_out(role)


@router.patch("/roles/{role_id}", response_model=RoleOut)
def update_role(
    role_id: UUID,
    payload: RoleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> RoleOut:
    _gone()


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> Response:
    _gone()


# --- Zuweisungen allgemein (globaler Admin) -------------------------------


@router.get("/roles/{role_id}/assignments", response_model=list[RoleAssignmentOut])
def list_role_assignments(
    role_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[RoleAssignmentOut]:
    role = roles.get_role(db, role_id)
    if role.name != "admin":
        _gone()
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
    _gone()


@router.delete("/roles/assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_assignment(
    assignment_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> Response:
    _gone()


# --- Eintragsrollen (Recht `admin` am Eintrag) ----------------------------


@router.get("/entries/{entry_id}/roles", response_model=list[RoleAssignmentOut])
def list_entry_roles(
    entry_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[RoleAssignmentOut]:
    _gone()


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
    _gone()


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
    _gone()


# --- Systemrollen je Benutzer (globaler Admin) ----------------------------


@router.get("/users/{user_id}/roles", response_model=list[RoleAssignmentOut])
def list_user_roles(
    user_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[RoleAssignmentOut]:
    # Nur noch Admin-Zuweisungen; fachliche Rollen existieren nach dem Cleanup
    # nicht mehr.
    return [
        _assignment_out(a)
        for a in roles.list_system_assignments(db, user_id)
        if a.role is not None and a.role.name == "admin"
    ]


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
    _gone()


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
    _gone()
