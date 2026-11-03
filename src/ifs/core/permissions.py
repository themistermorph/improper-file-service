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

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import BadRequest, NotFound
from ..models import ACL, PrincipalType, SystemPermission
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
