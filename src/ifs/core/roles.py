"""Rollen: benannte Rechtebündel (system- oder ressourcenweit) und Zuweisungen.

- **Systemrollen** gelten global (z. B. `admin`).
- **Ressourcenrollen** werden auf einen Eintrag zugewiesen und vererben sich.
- Das bestehende ACL-Modell bleibt unverändert nutzbar; Rollen kommen additiv dazu.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, contains_eager

from ..errors import BadRequest, Conflict, NotFound
from ..models import PrincipalType, Role, RoleAssignment, RoleScope
from . import accounts, authz

# (name, scope, permissions, beschreibung)
BUILTIN_ROLES: list[tuple[str, RoleScope, int, str]] = [
    ("admin", RoleScope.system, authz.ALL, "Vollzugriff (systemweit)"),
    ("auditor", RoleScope.system, 0, "Audit-Einblick (reserviert)"),
    ("user", RoleScope.system, 0, "Standardbenutzer"),
    ("viewer", RoleScope.resource, authz.READ, "Nur lesen"),
    ("collaborator", RoleScope.resource, authz.READ | authz.WRITE, "Lesen und schreiben"),
    (
        "editor",
        RoleScope.resource,
        authz.READ | authz.WRITE | authz.DELETE,
        "Lesen, schreiben, löschen",
    ),
    ("sharer", RoleScope.resource, authz.READ | authz.SHARE, "Lesen und teilen"),
    ("manager", RoleScope.resource, authz.ALL, "Vollzugriff auf den Eintrag"),
]


def ensure_builtin_roles(db: Session) -> None:
    # Eine Sammelabfrage statt einer Einzelabfrage je eingebauter Rolle.
    names = [name for name, _, _, _ in BUILTIN_ROLES]
    existing = set(
        db.execute(select(Role.name).where(Role.name.in_(names))).scalars().all()
    )
    for name, scope, permissions, description in BUILTIN_ROLES:
        if name in existing:
            continue
        db.add(
            Role(
                name=name,
                scope=scope,
                permissions=permissions,
                description=description,
                is_builtin=True,
            )
        )
    db.flush()


def find_by_name(db: Session, name: str) -> Role | None:
    return db.execute(select(Role).where(Role.name == name)).scalars().first()


def list_roles(db: Session, scope: RoleScope | None = None) -> list[Role]:
    stmt = select(Role)
    if scope is not None:
        stmt = stmt.where(Role.scope == scope)
    return list(db.execute(stmt.order_by(Role.scope, Role.name)).scalars().all())


def get_role(db: Session, role_id: UUID) -> Role:
    role = db.get(Role, role_id)
    if role is None:
        raise NotFound("Rolle nicht gefunden")
    return role


def create_role(
    db: Session,
    *,
    name: str,
    scope: RoleScope,
    permissions: int,
    description: str | None = None,
) -> Role:
    name = (name or "").strip()
    if not name:
        raise BadRequest("Rollenname darf nicht leer sein")
    if find_by_name(db, name) is not None:
        raise Conflict("Rolle mit diesem Namen existiert bereits")
    role = Role(
        name=name,
        scope=scope,
        permissions=permissions,
        description=description,
        is_builtin=False,
    )
    db.add(role)
    db.flush()
    return role


def update_role(
    db: Session,
    role: Role,
    *,
    description: str | None = None,
    permissions: int | None = None,
) -> Role:
    if role.is_builtin and permissions is not None and permissions != role.permissions:
        raise Conflict("Eingebaute Rollen können nicht in ihren Rechten geändert werden")
    if description is not None:
        role.description = description or None
    if permissions is not None:
        role.permissions = permissions
    db.flush()
    return role


def delete_role(db: Session, role: Role) -> None:
    if role.is_builtin:
        raise Conflict("Eingebaute Rollen können nicht gelöscht werden")
    # Sammel-DELETE: eine Abfrage statt Laden + Löschen je Zuweisung.
    db.execute(delete(RoleAssignment).where(RoleAssignment.role_id == role.id))
    db.delete(role)
    db.flush()


def list_assignments(
    db: Session,
    *,
    role_id: UUID | None = None,
    entry_id: UUID | None = None,
    principal_id: UUID | None = None,
) -> list[RoleAssignment]:
    stmt = select(RoleAssignment)
    if role_id is not None:
        stmt = stmt.where(RoleAssignment.role_id == role_id)
    if entry_id is not None:
        stmt = stmt.where(RoleAssignment.entry_id == entry_id)
    if principal_id is not None:
        stmt = stmt.where(RoleAssignment.principal_id == principal_id)
    return list(db.execute(stmt.order_by(RoleAssignment.created_at)).scalars().all())


def list_system_assignments(db: Session, user_id: UUID) -> list[RoleAssignment]:
    """Systemweite Rollenzuweisungen eines Benutzers – Filterung in der DB."""
    stmt = (
        select(RoleAssignment)
        .join(Role, Role.id == RoleAssignment.role_id)
        .options(contains_eager(RoleAssignment.role))
        .where(
            RoleAssignment.principal_id == user_id,
            RoleAssignment.principal_type == PrincipalType.user,
            RoleAssignment.entry_id.is_(None),
            Role.scope == RoleScope.system,
        )
        .order_by(RoleAssignment.created_at)
    )
    return list(db.execute(stmt).scalars().all())


def _find_assignment(
    db: Session,
    *,
    role_id: UUID,
    principal_type: PrincipalType,
    principal_id: UUID,
    entry_id: UUID | None,
) -> RoleAssignment | None:
    """Sucht exakt eine Zuweisung (inkl. ``entry_id IS NULL``) direkt in der DB."""
    entry_filter = (
        RoleAssignment.entry_id.is_(None)
        if entry_id is None
        else RoleAssignment.entry_id == entry_id
    )
    stmt = (
        select(RoleAssignment)
        .where(
            RoleAssignment.role_id == role_id,
            RoleAssignment.principal_type == principal_type,
            RoleAssignment.principal_id == principal_id,
            entry_filter,
        )
        .order_by(RoleAssignment.created_at)
    )
    return db.execute(stmt).scalars().first()


def assign_role(
    db: Session,
    *,
    role: Role,
    principal_type: PrincipalType,
    principal_id: UUID,
    entry_id: UUID | None = None,
) -> RoleAssignment:
    if role.scope == RoleScope.system:
        if entry_id is not None:
            raise BadRequest("Systemrollen werden nicht auf Einträge zugewiesen")
    elif entry_id is None:
        raise BadRequest("Ressourcenrollen benötigen einen Eintrag")

    if not accounts.principal_exists(db, principal_type, principal_id):
        raise BadRequest("Principal (Benutzer/Gruppe) nicht gefunden")

    # Doppelte Zuweisung direkt per Query prüfen statt alle Zuweisungen zu laden.
    duplicate = _find_assignment(
        db,
        role_id=role.id,
        principal_type=principal_type,
        principal_id=principal_id,
        entry_id=entry_id,
    )
    if duplicate is not None:
        return duplicate

    assignment = RoleAssignment(
        role_id=role.id,
        principal_type=principal_type,
        principal_id=principal_id,
        entry_id=entry_id,
    )
    db.add(assignment)
    db.flush()
    return assignment


def get_assignment(db: Session, assignment_id: UUID) -> RoleAssignment:
    assignment = db.get(RoleAssignment, assignment_id)
    if assignment is None:
        raise NotFound("Zuweisung nicht gefunden")
    return assignment


def remove_assignment(db: Session, assignment: RoleAssignment) -> None:
    db.delete(assignment)
    db.flush()
