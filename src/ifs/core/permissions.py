"""Berechtigungen: ACL-Grants und direkte Systemrechte.

Die Umstellung von Rollen auf Berechtigungen nutzt zwei getrennte Speicher:

* ``acl`` für Eintragsrechte (``read``/``write``/``delete``/``share``/
  ``publish``/``admin``), vererbt entlang des Pfades,
* ``system_permissions`` für ``audit``/``monitor``, global je Principal.

Die Funktionen hier kapseln Validierung und Upsert, damit API, Migration und
Tests dieselben Regeln verwenden.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..errors import BadRequest, NotFound
from ..models import (
    ACL,
    PrincipalType,
    Role,
    RoleAssignment,
    RoleScope,
    SystemPermission,
    User,
)
from . import authz


def upsert_acl(
    db: Session,
    *,
    entry_id: UUID,
    principal_type: PrincipalType,
    principal_id: UUID,
    perms: int,
    merge: bool = True,
) -> ACL:
    """Legt einen ACL-Grant an oder aktualisiert ihn.

    ``merge=True`` (POST) vereinigt die Bits mit einem bestehenden Grant – so
    bleibt das frühere Verhalten mehrerer Zuweisungen erhalten. ``merge=False``
    (PUT) ersetzt die Rechte exakt.
    """
    authz.validate_entry_perms(perms)
    perms &= authz.ALL_RESOURCE
    acl = db.execute(
        select(ACL).where(
            ACL.entry_id == entry_id,
            ACL.principal_type == principal_type,
            ACL.principal_id == principal_id,
        )
    ).scalars().first()
    if acl is None:
        if perms == 0:
            raise BadRequest("ACL benötigt mindestens ein Recht")
        acl = ACL(
            entry_id=entry_id,
            principal_type=principal_type,
            principal_id=principal_id,
            perms=perms,
            inherited=False,
        )
        db.add(acl)
    else:
        acl.perms = (int(acl.perms or 0) | perms) if merge else perms
        acl.inherited = False
    db.flush()
    return acl


def list_entry_acls(db: Session, entry_id: UUID) -> list[ACL]:
    return list(
        db.execute(
            select(ACL)
            .where(ACL.entry_id == entry_id)
            .order_by(ACL.created_at, ACL.id)
        ).scalars().all()
    )


def get_acl(db: Session, acl_id: UUID) -> ACL:
    acl = db.get(ACL, acl_id)
    if acl is None:
        raise NotFound("ACL nicht gefunden")
    return acl


def delete_acl(db: Session, acl: ACL) -> None:
    db.delete(acl)
    db.flush()


def get_system_permission(
    db: Session, principal_type: PrincipalType, principal_id: UUID
) -> SystemPermission | None:
    return db.execute(
        select(SystemPermission).where(
            SystemPermission.principal_type == principal_type,
            SystemPermission.principal_id == principal_id,
        )
    ).scalars().first()


def set_system_permissions(
    db: Session,
    *,
    principal_type: PrincipalType,
    principal_id: UUID,
    perms: int,
) -> SystemPermission | None:
    """Setzt ``audit``/``monitor`` exakt; ``0`` entfernt den Grant.

    ``ADMIN`` und alle Eintragsrechte werden abgelehnt. Die Rückgabe ist
    ``None``, wenn der Datensatz durch das Setzen auf 0 gelöscht wurde.
    """
    authz.validate_system_grant_perms(perms)
    row = get_system_permission(db, principal_type, principal_id)
    if perms == 0:
        if row is not None:
            db.delete(row)
            db.flush()
        return None
    if row is None:
        row = SystemPermission(
            principal_type=principal_type,
            principal_id=principal_id,
            permissions=perms,
        )
        db.add(row)
    else:
        row.permissions = perms
    db.flush()
    return row


def merge_system_permissions(
    db: Session,
    *,
    principal_type: PrincipalType,
    principal_id: UUID,
    perms: int,
) -> SystemPermission | None:
    """Vereinigt System-Bits mit einem bestehenden Grant (für den Backfill)."""
    authz.validate_system_grant_perms(perms)
    row = get_system_permission(db, principal_type, principal_id)
    if row is None:
        if perms == 0:
            return None
        row = SystemPermission(
            principal_type=principal_type,
            principal_id=principal_id,
            permissions=perms,
        )
        db.add(row)
    else:
        row.permissions = int(row.permissions or 0) | perms
    db.flush()
    return row


def list_system_permissions(db: Session) -> list[SystemPermission]:
    return list(
        db.execute(
            select(SystemPermission).order_by(
                SystemPermission.principal_type, SystemPermission.principal_id
            )
        ).scalars().all()
    )


def _find_admin_assignment(
    db: Session,
    *,
    role_id: UUID,
    principal_type: PrincipalType,
    principal_id: UUID,
) -> RoleAssignment | None:
    return db.execute(
        select(RoleAssignment).where(
            RoleAssignment.role_id == role_id,
            RoleAssignment.principal_type == principal_type,
            RoleAssignment.principal_id == principal_id,
            RoleAssignment.entry_id.is_(None),
        )
    ).scalars().first()


def _ensure_admin_assignment(
    db: Session,
    admin_role: Role,
    principal_type: PrincipalType,
    principal_id: UUID,
) -> bool:
    if _find_admin_assignment(
        db,
        role_id=admin_role.id,
        principal_type=principal_type,
        principal_id=principal_id,
    ) is not None:
        return False
    db.add(
        RoleAssignment(
            role_id=admin_role.id,
            principal_type=principal_type,
            principal_id=principal_id,
            entry_id=None,
        )
    )
    db.flush()
    return True


def _ensure_admin_role(db: Session) -> Role:
    admin = db.execute(select(Role).where(Role.name == "admin")).scalars().first()
    if admin is None:
        admin = Role(
            name="admin",
            scope=RoleScope.system,
            permissions=authz.ALL,
            description="Vollzugriff (systemweit)",
            is_builtin=True,
        )
        db.add(admin)
        db.flush()
    else:
        # Neue Bits (publish/audit/monitor) müssen auch in bestehenden
        # Installationen in der Adminrolle ankommen.
        admin.permissions = authz.ALL
        admin.scope = RoleScope.system
        admin.is_builtin = True
        db.flush()
    return admin


def backfill_roles(db: Session) -> dict[str, int]:
    """Migriert Rollen/Zuweisungen additiv nach ACL bzw. System-Grants.

    Nicht destruktiv: Rollen und ``role_assignments`` bleiben erhalten. Die
    Funktion ist idempotent; wiederholte Aufrufe erzeugen keine Duplikate und
    ändern keine bereits migrierten Rechte. Sie spiegelt
    ``migrations/sql/0012_permissions_backfill.sql``.
    """
    stats = {
        "acl_grants": 0,
        "system_grants": 0,
        "admin_assignments": 0,
        "publish_from_share": 0,
    }
    admin_role = _ensure_admin_role(db)

    rows = db.execute(
        select(RoleAssignment, Role).join(Role, Role.id == RoleAssignment.role_id)
    ).all()
    for assignment, role in rows:
        if role.scope == RoleScope.resource and assignment.entry_id is not None:
            perms = int(role.permissions or 0) & authz.ALL_RESOURCE
            # Früher prüfte „Veröffentlichen“ `share`; das Verhalten bleibt
            # erhalten, indem bestehende share-Grants zusätzlich publish erhalten.
            if perms & authz.SHARE:
                perms |= authz.PUBLISH
            if perms:
                before = db.execute(
                    select(ACL).where(
                        ACL.entry_id == assignment.entry_id,
                        ACL.principal_type == assignment.principal_type,
                        ACL.principal_id == assignment.principal_id,
                    )
                ).scalars().first()
                old = int(before.perms or 0) if before is not None else 0
                upsert_acl(
                    db,
                    entry_id=assignment.entry_id,
                    principal_type=assignment.principal_type,
                    principal_id=assignment.principal_id,
                    perms=perms,
                    merge=True,
                )
                if old | perms != old:
                    stats["acl_grants"] += 1
            continue

        if role.scope != RoleScope.system or assignment.entry_id is not None:
            continue

        # `admin` bleibt die Adminrolle; `auditor` war ein reservierter
        # Platzhalter ohne Bits und wird zu einem echten AUDIT-Grant.
        if role.name == "auditor":
            system_bits = authz.AUDIT
        elif role.name == "admin":
            system_bits = 0
        else:
            system_bits = int(role.permissions or 0) & authz.SYSTEM_ASSIGNABLE
        if system_bits:
            existing = get_system_permission(
                db, assignment.principal_type, assignment.principal_id
            )
            old = int(existing.permissions or 0) if existing is not None else 0
            merge_system_permissions(
                db,
                principal_type=assignment.principal_type,
                principal_id=assignment.principal_id,
                perms=system_bits,
            )
            if old | system_bits != old:
                stats["system_grants"] += 1
        if (
            role.name != "admin"
            and (int(role.permissions or 0) & authz.ADMIN)
            and _ensure_admin_assignment(
                db, admin_role, assignment.principal_type, assignment.principal_id
            )
        ):
            stats["admin_assignments"] += 1

    # Bootstrap-Flag in die Adminrolle überführen (bleibt als Legacy lesbar).
    for user in db.execute(select(User).where(User.is_admin.is_(True))).scalars().all():
        if _ensure_admin_assignment(
            db, admin_role, PrincipalType.user, user.id
        ):
            stats["admin_assignments"] += 1

    # Bestehende ACLs mit `share` erhalten `publish` (Verhalten erhalten).
    for acl in db.execute(select(ACL).where(ACL.perms.op("&")(authz.SHARE) != 0)).scalars():
        if not (int(acl.perms or 0) & authz.PUBLISH):
            acl.perms = int(acl.perms or 0) | authz.PUBLISH
            stats["publish_from_share"] += 1
    db.flush()
    return stats


def cleanup_roles(db: Session) -> dict[str, int]:
    """Destruktiver Abschluss: nur die Adminrolle bleibt übrig.

    Voraussetzung ist ein erfolgreicher, verifizierter Backfill. Die Funktion
    ist idempotent und entfernt alle fachlichen Rollen samt Zuweisungen.
    """
    admin_role = _ensure_admin_role(db)
    stats = {
        "assignments_removed": 0,
        "roles_removed": 0,
        "admin_assignments": 0,
    }
    non_admin = db.execute(select(Role.id).where(Role.name != "admin")).scalars().all()
    if non_admin:
        result = db.execute(
            delete(RoleAssignment).where(RoleAssignment.role_id.in_(non_admin))
        )
        stats["assignments_removed"] = int(result.rowcount or 0)
        result = db.execute(delete(Role).where(Role.id.in_(non_admin)))
        stats["roles_removed"] = int(result.rowcount or 0)

    # Entry-gebundene Admin-Zuweisungen sind nach der Umstellung ungültig.
    result = db.execute(
        delete(RoleAssignment).where(RoleAssignment.entry_id.isnot(None))
    )
    stats["assignments_removed"] += int(result.rowcount or 0)

    for user in db.execute(select(User).where(User.is_admin.is_(True))).scalars().all():
        if _ensure_admin_assignment(
            db, admin_role, PrincipalType.user, user.id
        ):
            stats["admin_assignments"] += 1
    db.flush()
    return stats

