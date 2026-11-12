"""Zentrales Autorisierungsmodul (PDP).

Wird von HTTP- und FTPS-Adapter identisch genutzt (Architekturprinzip P1/P4).
Rechte werden additiv entlang des Pfades vererbt: ein Recht auf einem Ordner
gilt auch für alle Nachfahren.

Quellen effektiver Rechte:
- Besitz (Owner erhält alle *Eintrags*rechte),
- ACLs entlang des Pfades,
- übergangsweise Ressourcenrollen (nach der Migration nur noch ACLs).

Systemweite Rollen/Grants gewähren **keinen** Dateizugriff; Systemrechte
(``audit``/``monitor``/``api``) werden getrennt über ``system_perms`` geprüft.

Performance: Die Vorfahrenkette wird in einer Query über eine rekursive CTE
bestimmt; ACLs und Ressourcenrollen werden jeweils für die *gesamte* Kette in
einer Query geladen. Ein begrenzter Memo-Cache in ``session.info`` vermeidet
Wiederholungen innerhalb derselben Session und wird bei jedem Flush verworfen.
"""

from __future__ import annotations

from sqlalchemy import and_, event, or_, select
from sqlalchemy.orm import Session, aliased

from ..errors import PermissionDenied
from ..models import (
    ACL,
    Entry,
    PrincipalType,
    Role,
    RoleAssignment,
    RoleScope,
    SystemPermission,
    User,
)

READ = 1
WRITE = 2
DELETE = 4
SHARE = 8
# `ADMIN` ist absichtlich doppelt belegt, aber namensraumgetrennt:
# - systemweiter ADMIN-Grant/Rolle -> Systemverwaltung,
# - Eintrags-ADMIN-ACL -> Zugriff auf diesen Eintrag verwalten.
ADMIN = 16
PUBLISH = 32
AUDIT = 64
MONITOR = 128
# Erlaubt Bearer-Tokens für API-Clients auch außerhalb der in
# `IFS_BEARER_ALLOWED_NETWORKS` freigegebenen Netze. Die Web-UI nutzt
# ausschließlich HttpOnly-Cookies.
API = 256

# Eintragsrechte (ACL) und Systemrechte (system_permissions) sind getrennte
# Namensräume; `ALL` bleibt die Summe beider für die Adminrolle.
ALL_RESOURCE = READ | WRITE | DELETE | SHARE | PUBLISH | ADMIN
ALL_SYSTEM = ADMIN | AUDIT | MONITOR | API
ALL = ALL_RESOURCE | ALL_SYSTEM

# Nur diese Bits dürfen direkt als System-Grant vergeben werden. ADMIN kommt
# ausschließlich über die Adminrolle bzw. das Bootstrap-Flag `users.is_admin`.
SYSTEM_ASSIGNABLE = AUDIT | MONITOR | API

ENTRY_PERM_NAMES: dict[str, int] = {
    "read": READ,
    "write": WRITE,
    "delete": DELETE,
    "share": SHARE,
    "admin": ADMIN,
    "publish": PUBLISH,
}

SYSTEM_PERM_NAMES: dict[str, int] = {
    "admin": ADMIN,
    "audit": AUDIT,
    "monitor": MONITOR,
    "api": API,
}

# Vereinigung beider Namensräume für Anzeigezwecke (z. B. Adminrolle).
PERM_NAMES: dict[str, int] = {**ENTRY_PERM_NAMES, **SYSTEM_PERM_NAMES}

ACTION_PERM: dict[str, int] = {
    "read": READ,
    "write": WRITE,
    "delete": DELETE,
    "share": SHARE,
    "admin": ADMIN,
    "publish": PUBLISH,
    # Systemrechte werden nicht über `can()` geprüft; die Einträge bleiben für
    # `ACTION_PERM`-Konsistenz und Fehlermeldungen erhalten.
    "audit": AUDIT,
    "monitor": MONITOR,
    "api": API,
}

# Begrenzter Memo-Cache je Session (siehe Modul-Docstring).
_CACHE_KEY = "ifs.authz_cache"
_CACHE_MAX = 1024


def _invalidate_cache(session: Session, flush_context) -> None:
    """Verwirft den Memo-Cache bei jedem Flush – Änderungen wirken sofort."""
    session.info.pop(_CACHE_KEY, None)


def _invalidate_on_bulk_dml(state) -> None:
    """Verwirft den Memo-Cache auch bei Bulk-DML über ``Session.execute``.

    ``session.execute(delete(...))``/``update(...)`` umgeht den Flush und damit
    ``after_flush``; die Änderung ist aber sofort in der Datenbank sichtbar.
    Ohne diesen Haken könnte eine spätere Rechteprüfung in derselben Session
    veraltete (z. B. bereits entzogene) Rechte liefern.
    """
    if state.is_delete or state.is_update or state.is_insert:
        state.session.info.pop(_CACHE_KEY, None)


def _invalidate_on_rollback(session: Session, previous_transaction) -> None:
    """Verwirft den Memo-Cache bei (Teil-)Rollbacks.

    Ein Savepoint-Rollback stellt gelöschte ACLs/Rollen wieder her, ohne zu
    flushen; ein zuvor berechneter (verneinender) Cache-Eintrag wäre sonst
    veraltet und würde gültige Rechte verweigern.
    """
    session.info.pop(_CACHE_KEY, None)


def _register_cache_invalidation() -> None:
    """Registriert die Cache-Invalidierung idempotent (einmal pro Prozess)."""
    if not event.contains(Session, "after_flush", _invalidate_cache):
        event.listen(Session, "after_flush", _invalidate_cache)
    if not event.contains(Session, "do_orm_execute", _invalidate_on_bulk_dml):
        event.listen(Session, "do_orm_execute", _invalidate_on_bulk_dml)
    if not event.contains(Session, "after_soft_rollback", _invalidate_on_rollback):
        event.listen(Session, "after_soft_rollback", _invalidate_on_rollback)


_register_cache_invalidation()


def _cache_get(session: Session, key):
    cache = session.info.get(_CACHE_KEY)
    if not cache or key not in cache:
        return None
    value = cache.pop(key)
    # LRU: zuletzt genutzter Eintrag wandert ans Ende der Einfügereihenfolge.
    cache[key] = value
    return value


def _cache_put(session: Session, key, value) -> None:
    cache = session.info.get(_CACHE_KEY)
    if cache is None:
        cache = {}
        session.info[_CACHE_KEY] = cache
    elif key not in cache and len(cache) >= _CACHE_MAX:
        # Begrenzt: der älteste Eintrag fällt heraus.
        cache.pop(next(iter(cache)), None)
    cache[key] = value


def perms_from_names(names: list[str], *, namespace: str = "entry") -> int:
    """Wandelt Rechte-Namen in eine Bitmaske um.

    ``namespace`` trennt Eintrags- und Systemrechte: Ein System-Bit in einer
    ACL oder ein Eintrags-Bit in einem System-Grant ist ein Programmierfehler
    und wird abgelehnt. ``namespace="all"`` erlaubt beide (nur für Rollen).
    """
    allowed = PERM_NAMES
    if namespace == "entry":
        allowed = ENTRY_PERM_NAMES
    elif namespace == "system":
        allowed = SYSTEM_PERM_NAMES
    elif namespace != "all":
        raise ValueError(f"Unbekannter Rechte-Namensraum: {namespace}")
    perms = 0
    for name in names:
        if name not in PERM_NAMES:
            raise ValueError(f"Unbekanntes Recht: {name}")
        if name not in allowed:
            raise ValueError(f"Recht '{name}' ist im Namensraum '{namespace}' nicht erlaubt")
        perms |= PERM_NAMES[name]
    return perms


def names_from_perms(perms: int, *, namespace: str | None = None) -> list[str]:
    if namespace is None:
        mapping = PERM_NAMES
    elif namespace == "entry":
        mapping = ENTRY_PERM_NAMES
    elif namespace == "system":
        mapping = SYSTEM_PERM_NAMES
    elif namespace == "all":
        mapping = PERM_NAMES
    else:
        raise ValueError(f"Unbekannter Rechte-Namensraum: {namespace}")
    return [name for name, bit in mapping.items() if perms & bit]


def validate_entry_perms(perms: int) -> None:
    """Stellt sicher, dass eine ACL nur Eintragsrechte enthält."""
    if perms & ~ALL_RESOURCE:
        raise ValueError("Systemrechte dürfen nicht in einer ACL gespeichert werden")


def validate_system_grant_perms(perms: int) -> None:
    """System-Grants dürfen nur ``audit``/``monitor``/``api`` enthalten (kein ADMIN)."""
    if perms & ~SYSTEM_ASSIGNABLE:
        raise ValueError("System-Grants erlauben nur 'audit' und 'monitor'")



def _group_ids(db: Session, user: User) -> frozenset:
    """IDs der Gruppen des Benutzers – höchstens einmal je Session geladen."""
    key = ("groups", user.id)
    cached = _cache_get(db, key)
    if cached is not None:
        return cached
    ids = frozenset(group.id for group in user.groups)
    _cache_put(db, key, ids)
    return ids


def _principal_conditions(user: User, group_ids: frozenset):
    """OR-Bedingungen für Rollen-/System-Grant-Zeilen des Benutzers."""
    return _model_principal_conditions(RoleAssignment, user, group_ids)


def _model_principal_conditions(model, user: User, group_ids: frozenset):
    conditions = [
        and_(
            model.principal_type == PrincipalType.user,
            model.principal_id == user.id,
        )
    ]
    conditions.extend(
        and_(
            model.principal_type == PrincipalType.group,
            model.principal_id == group_id,
        )
        for group_id in group_ids
    )
    return conditions


def _role_perms(db: Session, conditions, entry_id) -> int:
    """Systemweite Rollenrechte (nur für den Adminstatus, nicht für Dateien)."""
    scope = (
        RoleAssignment.entry_id.is_(None)
        if entry_id is None
        else RoleAssignment.entry_id == entry_id
    )
    rows = db.execute(
        select(Role.permissions)
        .join(RoleAssignment, RoleAssignment.role_id == Role.id)
        .where(scope, Role.scope == RoleScope.system, or_(*conditions))
    ).scalars().all()
    perms = 0
    for value in rows:
        perms |= int(value or 0)
    return perms


def _resource_role_perms(db: Session, conditions, entry_id) -> int:
    """Übergangsweise Ressourcenrollen, strikt auf Eintragsbits begrenzt.

    Nach der Migration existieren keine Ressourcenrollen mehr; der Lesepfad
    bleibt für Rollback-Szenarien und den Dual-Read während des Rollouts aktiv.
    """
    rows = db.execute(
        select(Role.permissions)
        .join(RoleAssignment, RoleAssignment.role_id == Role.id)
        .where(
            RoleAssignment.entry_id == entry_id,
            Role.scope == RoleScope.resource,
            or_(*conditions),
        )
    ).scalars().all()
    perms = 0
    for value in rows:
        perms |= int(value or 0)
    return perms & ALL_RESOURCE


def _acl_applies(acl: ACL, user: User, group_ids: frozenset) -> bool:
    if acl.principal_type == PrincipalType.user:
        return acl.principal_id == user.id
    return acl.principal_id in group_ids


def _ancestor_ids(db: Session, entry: Entry) -> list:
    """IDs der Vorfahrenkette inklusive des Eintrags selbst – eine Query.

    Die rekursive CTE nutzt ``UNION`` statt ``UNION ALL``: ein bereits
    gesehener Knoten (Zyklus in beschädigten Daten) erzeugt eine doppelte
    Zeile und beendet die Rekursion – exakt wie die frühere ``seen``-Menge.
    Ist der Eintrag noch nicht persistiert, liefert die CTE nichts; der
    Aufrufer fällt dann auf die iterative ORM-Kette zurück.
    """
    base = (
        select(Entry.id.label("id"), Entry.parent_id.label("parent_id"))
        .where(Entry.id == entry.id)
        .cte("ifs_ancestry", recursive=True)
    )
    parent = aliased(Entry, name="ifs_ancestry_parent")
    base = base.union(
        select(parent.id.label("id"), parent.parent_id.label("parent_id")).where(
            parent.id == base.c.parent_id
        )
    )
    return list(db.execute(select(base.c.id)).scalars().all())


def _effective_perms_walk(db: Session, user: User, entry: Entry, group_ids: frozenset) -> int:
    """Fallback für nicht persistierte Einträge – wie früher, iterativ."""
    conditions = _principal_conditions(user, group_ids)
    perms = 0
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
        perms |= _resource_role_perms(db, conditions, node.id)
        node = node.parent
    return perms


def _compute_perms(db: Session, user: User, entry: Entry) -> int:
    """Berechnet die vererbten Rechte ohne Cache (drei Queries unabhängig von der Tiefe)."""
    group_ids = _group_ids(db, user)
    ids = _ancestor_ids(db, entry)
    if not ids:
        return _effective_perms_walk(db, user, entry, group_ids)

    perms = 0
    acls = db.execute(select(ACL).where(ACL.entry_id.in_(ids))).scalars().all()
    for acl in acls:
        if _acl_applies(acl, user, group_ids):
            perms |= int(acl.perms or 0)

    conditions = _principal_conditions(user, group_ids)
    role_rows = db.execute(
        select(Role.permissions)
        .join(RoleAssignment, RoleAssignment.role_id == Role.id)
        .where(
            RoleAssignment.entry_id.in_(ids),
            Role.scope == RoleScope.resource,
            or_(*conditions),
        )
    ).scalars().all()
    for value in role_rows:
        perms |= int(value or 0)
    return perms & ALL_RESOURCE


def effective_perms(db: Session, user: User | None, entry: Entry | None) -> int:
    """Ermittelt die effektiven Rechte eines Nutzers auf einem Eintrag.

    Systemweite Rollen (auch ``admin``) gewähren **keinen** Dateizugriff: Zugriff
    entsteht ausschließlich über Besitz, Ressourcenrollen und ACLs. So hat auch ein
    Systemadministrator per Design keinen Zugriff auf fremde Wurzelbäume.
    """
    if user is None or entry is None:
        return 0
    # Besitz wird live am Objekt geprüft (In-Memory-Änderungen wirken sofort);
    # gecacht werden nur die aus ACLs/Ressourcenrollen abgeleiteten Rechte.
    if entry.owner_id == user.id:
        return ALL_RESOURCE

    key = ("perms", user.id, entry.id)
    cached = _cache_get(db, key)
    if cached is not None:
        return cached
    perms = _compute_perms(db, user, entry)
    _cache_put(db, key, perms)
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

    key = ("admin", user.id)
    cached = _cache_get(db, key)
    if cached is not None:
        return cached
    conditions = _principal_conditions(user, _group_ids(db, user))
    result = bool(_role_perms(db, conditions, None) & ADMIN)
    _cache_put(db, key, result)
    return result


def require_admin(db: Session, user: User | None) -> None:
    if not is_system_admin(db, user):
        raise PermissionDenied("Administratorrechte erforderlich")


def system_perms(db: Session, user: User | None) -> int:
    """Systemweite Rechte (``admin``/``audit``/``monitor``) eines Benutzers.

    Liest ausschließlich ``users.is_admin``, die systemweite Adminrolle und
    ``system_permissions``. ACLs und Eintragsrechte werden bewusst **nicht**
    eingemischt; umgekehrt gelangen diese Bits nie in ``effective_perms``.
    """
    if user is None:
        return 0
    key = ("system_perms", user.id)
    cached = _cache_get(db, key)
    if cached is not None:
        return cached

    perms = ADMIN if is_system_admin(db, user) else 0
    conditions = _model_principal_conditions(
        SystemPermission, user, _group_ids(db, user)
    )
    rows = db.execute(
        select(SystemPermission.permissions).where(or_(*conditions))
    ).scalars().all()
    for value in rows:
        perms |= int(value or 0)
    perms &= ALL_SYSTEM
    _cache_put(db, key, perms)
    return perms


def has_system_perm(db: Session, user: User | None, bit: int) -> bool:
    return bool(system_perms(db, user) & bit)


def require_system_perm(db: Session, user: User | None, bit: int) -> None:
    if not has_system_perm(db, user, bit):
        name = names_from_perms(bit, namespace="system")
        label = ", ".join(name) if name else str(bit)
        raise PermissionDenied(f"Systemrecht '{label}' erforderlich")
