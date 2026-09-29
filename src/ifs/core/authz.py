"""Zentrales Autorisierungsmodul (PDP).

Wird von HTTP- und FTPS-Adapter identisch genutzt (Architekturprinzip P1/P4).
Rechte werden additiv entlang des Pfades vererbt: ein Recht auf einem Ordner
gilt auch für alle Nachfahren.

Quellen effektiver Rechte:
- Besitz (Owner erhält alle Rechte),
- systemweite Rollen (global, `entry_id IS NULL`),
- Ressourcenrollen und ACLs entlang des Pfades.
"""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..errors import PermissionDenied
from ..models import ACL, Entry, PrincipalType, Role, RoleAssignment, User

READ = 1
WRITE = 2
DELETE = 4
SHARE = 8
ADMIN = 16
ALL = READ | WRITE | DELETE | SHARE | ADMIN

ACTION_PERM: dict[str, int] = {
    "read": READ,
    "write": WRITE,
    "delete": DELETE,
    "share": SHARE,
    "admin": ADMIN,
}

PERM_NAMES: dict[str, int] = {
    "read": READ,
    "write": WRITE,
    "delete": DELETE,
    "share": SHARE,
    "admin": ADMIN,
}


def perms_from_names(names: list[str]) -> int:
    perms = 0
    for name in names:
        if name not in PERM_NAMES:
            raise ValueError(f"Unbekanntes Recht: {name}")
        perms |= PERM_NAMES[name]
    return perms


def names_from_perms(perms: int) -> list[str]:
    return [name for name, bit in PERM_NAMES.items() if perms & bit]


def _group_ids(user: User) -> set:
    return {group.id for group in user.groups}


def _principal_conditions(user: User, group_ids: set):
    conditions = [
        and_(
            RoleAssignment.principal_type == PrincipalType.user,
            RoleAssignment.principal_id == user.id,
        )
    ]
    conditions.extend(
        and_(
            RoleAssignment.principal_type == PrincipalType.group,
            RoleAssignment.principal_id == group_id,
        )
        for group_id in group_ids
    )
    return conditions


def _role_perms(db: Session, conditions, entry_id) -> int:
    scope = (
        RoleAssignment.entry_id.is_(None)
        if entry_id is None
        else RoleAssignment.entry_id == entry_id
    )
    rows = db.execute(
        select(Role.permissions)
        .join(RoleAssignment, RoleAssignment.role_id == Role.id)
        .where(scope, or_(*conditions))
    ).scalars().all()
    perms = 0
    for value in rows:
        perms |= int(value or 0)
    return perms


def _acl_applies(acl: ACL, user: User, group_ids: set) -> bool:
    if acl.principal_type == PrincipalType.user:
        return acl.principal_id == user.id
    return acl.principal_id in group_ids


def effective_perms(db: Session, user: User | None, entry: Entry | None) -> int:
    """Ermittelt die effektiven Rechte eines Nutzers auf einem Eintrag.

    Systemweite Rollen (auch ``admin``) gewähren **keinen** Dateizugriff: Zugriff
    entsteht ausschließlich über Besitz, Ressourcenrollen und ACLs. So hat auch ein
    Systemadministrator per Design keinen Zugriff auf fremde Wurzelbäume.
    """
    if user is None or entry is None:
        return 0

    group_ids = _group_ids(user)
    conditions = _principal_conditions(user, group_ids)

    perms = 0
    if entry.owner_id == user.id:
        perms |= ALL

    node: Entry | None = entry
    seen: set = set()
    while node is not None:
        if node.id in seen:
            # Defense-in-Depth: Ein Zyklus im Baum (durch die Zeilensperre in
            # ``namespace.move`` verhindert) darf hier nicht endlos laufen.
            break
        seen.add(node.id)
        acls = db.execute(select(ACL).where(ACL.entry_id == node.id)).scalars().all()
        for acl in acls:
            if _acl_applies(acl, user, group_ids):
                perms |= acl.perms
        perms |= _role_perms(db, conditions, node.id)
        node = node.parent
    return perms


def can(db: Session, user: User | None, action: str, entry: Entry | None) -> bool:
    required = ACTION_PERM.get(action)
    if required is None:
        raise ValueError(f"Unbekannte Aktion: {action}")
    return bool(effective_perms(db, user, entry) & required)


def authorize(db: Session, user: User | None, action: str, entry: Entry | None) -> None:
    if not can(db, user, action, entry):
        target = str(entry.id) if entry else "-"
        actor = user.username if user else "anonymous"
        raise PermissionDenied(f"{actor} darf '{action}' auf {target} nicht ausführen")


def is_system_admin(db: Session, user: User | None) -> bool:
    """Globaler Admin – über das Flag oder die systemweite Rolle `admin`."""
    if user is None:
        return False
    if user.is_admin:
        return True
    conditions = _principal_conditions(user, _group_ids(user))
    return bool(_role_perms(db, conditions, None) & ADMIN)


def require_admin(db: Session, user: User | None) -> None:
    if not is_system_admin(db, user):
        raise PermissionDenied("Administratorrechte erforderlich")
