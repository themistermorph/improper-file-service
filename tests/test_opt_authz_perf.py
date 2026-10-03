"""Performance- und Verhaltens-Tests für das optimierte Autorisierungsmodul.

Gezählt werden echte SQL-Statements über ``before_cursor_execute``:
``authorize`` muss unabhängig von der Tiefe des Baums mit wenigen Queries
auskommen, während Besitz, Vererbung, Gruppen-Rollen und der Per-Session-Cache
(inklusive Invalidierung bei jedem Flush) unverändert korrekt bleiben.
"""

from __future__ import annotations

import contextlib

from sqlalchemy import event, update

from ifs.core import authz, namespace, roles
from ifs.db import get_engine
from ifs.models import ACL, Entry, Group, PrincipalType, RoleScope

from .conftest import make_user


@contextlib.contextmanager
def query_counter():
    """Zählt alle SQL-Statements, die innerhalb des Kontexts ausgeführt werden."""
    engine = get_engine()
    statements: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _count)


def _deep_chain(db, owner, depth: int = 20):
    """Baut einen Baum der Tiefe ``depth`` und liefert (Wurzel, Blatt)."""
    root = namespace.ensure_root(db, owner.id)
    node = root
    for index in range(depth):
        node = namespace.create_folder(db, node, f"ebene{index:02d}", owner.id)
    return root, node


def _grant_read(db, entry, principal_type, principal_id, perms: int = authz.READ) -> ACL:
    acl = ACL(
        entry_id=entry.id,
        principal_type=principal_type,
        principal_id=principal_id,
        perms=perms,
        inherited=True,
    )
    db.add(acl)
    db.flush()
    return acl


# --- Query-Zähler -----------------------------------------------------------


def test_authorize_queryzahl_deutlich_unter_2x_anzahl_vorfahren(db):
    owner = make_user(db, "owner-tief")
    other = make_user(db, "other-tief")
    root, leaf = _deep_chain(db, owner, depth=20)
    _grant_read(db, root, PrincipalType.user, other.id)

    with query_counter() as statements:
        authz.authorize(db, other, "read", leaf)

    # Tiefe 20 = 21 Knoten. Früher: 2 Queries je Knoten plus Parent-Lazy-Loads
    # (>= 42); jetzt: CTE + ACL + Rollen (ggf. einmalig Gruppenladen).
    assert len(statements) <= 4
    assert len(statements) < 21 * 2


def test_queryzahl_ist_unabhaengig_von_der_tiefe(db):
    owner = make_user(db, "owner-vergleich")
    other = make_user(db, "other-vergleich")
    root = namespace.ensure_root(db, owner.id)
    flat = namespace.create_folder(db, root, "flach", owner.id)
    _grant_read(db, root, PrincipalType.user, other.id)
    # Gruppen einmal warmladen, damit beide Messungen vergleichbar sind.
    authz.effective_perms(db, other, root)

    with query_counter() as flach:
        authz.authorize(db, other, "read", flat)

    node = flat
    for index in range(20):
        node = namespace.create_folder(db, node, f"tief{index:02d}", owner.id)

    with query_counter() as tief:
        authz.authorize(db, other, "read", node)

    assert len(flach) <= 4
    assert len(tief) <= 4
    assert len(tief) == len(flach)


def test_wiederholte_pruefung_nutzt_session_cache(db):
    owner = make_user(db, "owner-cache")
    other = make_user(db, "other-cache")
    root, leaf = _deep_chain(db, owner, depth=5)
    _grant_read(db, root, PrincipalType.user, other.id)

    assert authz.can(db, other, "read", leaf)  # füllt den Cache
    with query_counter() as statements:
        assert authz.can(db, other, "read", leaf)
    assert statements == []


def test_flush_invalidiert_den_cache(db):
    owner = make_user(db, "owner-flush")
    other = make_user(db, "other-flush")
    root, leaf = _deep_chain(db, owner, depth=3)
    _grant_read(db, root, PrincipalType.user, other.id)

    assert authz.can(db, other, "read", leaf)
    assert "ifs.authz_cache" in db.info

    _grant_read(db, leaf, PrincipalType.user, other.id, perms=authz.DELETE)
    assert "ifs.authz_cache" not in db.info


def test_cache_invalidierung_wird_nur_einmal_registriert():
    from sqlalchemy.orm import Session

    from ifs.core import authz as authz_module

    assert event.contains(Session, "after_flush", authz_module._invalidate_cache)
    authz_module._register_cache_invalidation()
    assert event.contains(Session, "after_flush", authz_module._invalidate_cache)


# --- Verhalten: Besitz, Vererbung, Gruppen, Entzug --------------------------


def test_besitz_gewaehrt_alle_rechte_und_wirkt_live(db):
    owner = make_user(db, "owner-besitz")
    other = make_user(db, "other-besitz")
    _root, leaf = _deep_chain(db, owner, depth=3)

    assert authz.effective_perms(db, owner, leaf) == authz.ALL
    assert authz.can(db, owner, "delete", leaf)

    # Besitzerwechsel ist nach dem Flush sofort sichtbar.
    leaf.owner_id = other.id
    db.flush()
    assert authz.effective_perms(db, owner, leaf) == 0
    assert not authz.can(db, owner, "read", leaf)
    assert authz.effective_perms(db, other, leaf) == authz.ALL


def test_gruppen_acl_und_rollen_vererbung(db):
    roles.ensure_builtin_roles(db)
    owner = make_user(db, "owner-gruppe")
    member = make_user(db, "member-gruppe")
    group = Group(name="team-gruppe")
    group.members.append(member)
    db.add(group)
    db.flush()

    root, leaf = _deep_chain(db, owner, depth=5)
    collaborator = roles.find_by_name(db, "collaborator")
    roles.assign_role(
        db,
        role=collaborator,
        principal_type=PrincipalType.group,
        principal_id=group.id,
        entry_id=root.id,
    )

    assert authz.can(db, member, "read", leaf)
    assert authz.can(db, member, "write", leaf)
    assert not authz.can(db, member, "delete", leaf)

    # Gruppenmitgliedschaft entziehen -> geerbte Rechte verschwinden nach Flush.
    group.members.remove(member)
    db.flush()
    assert not authz.can(db, member, "read", leaf)
    assert authz.effective_perms(db, member, leaf) == 0


def test_gruppen_ids_werden_hoechstens_einmal_geladen(db):
    owner = make_user(db, "owner-gruppencache")
    member = make_user(db, "member-gruppencache")
    group = Group(name="team-gruppencache")
    group.members.append(member)
    db.add(group)
    db.flush()

    root = namespace.ensure_root(db, owner.id)
    first = namespace.create_folder(db, root, "eins", owner.id)
    second = namespace.create_folder(db, root, "zwei", owner.id)
    _grant_read(db, root, PrincipalType.group, group.id)

    with query_counter() as erster_aufruf:
        assert authz.can(db, member, "read", first)
    with query_counter() as zweiter_aufruf:
        assert authz.can(db, member, "read", second)

    assert len(erster_aufruf) <= 4
    # Zweiter Eintrag: nur CTE + ACL + Rollen, keine erneute Gruppenladung.
    assert len(zweiter_aufruf) <= 3


def test_acl_aenderung_nach_flush_sofort_sichtbar(db):
    owner = make_user(db, "owner-acl")
    other = make_user(db, "other-acl")
    root, leaf = _deep_chain(db, owner, depth=3)
    acl = _grant_read(db, root, PrincipalType.user, other.id)

    assert authz.can(db, other, "read", leaf)
    assert not authz.can(db, other, "write", leaf)

    acl.perms = authz.READ | authz.WRITE
    db.flush()
    assert authz.can(db, other, "write", leaf)

    db.delete(acl)
    db.flush()
    assert not authz.can(db, other, "read", leaf)
    assert authz.effective_perms(db, other, leaf) == 0


def test_rollen_entzug_nach_flush_sofort_sichtbar(db):
    roles.ensure_builtin_roles(db)
    owner = make_user(db, "owner-rollenentzug")
    reader = make_user(db, "reader-rollenentzug")
    root, leaf = _deep_chain(db, owner, depth=3)
    viewer = roles.find_by_name(db, "viewer")
    assignment = roles.assign_role(
        db,
        role=viewer,
        principal_type=PrincipalType.user,
        principal_id=reader.id,
        entry_id=root.id,
    )

    assert authz.can(db, reader, "read", leaf)

    roles.remove_assignment(db, assignment)
    assert not authz.can(db, reader, "read", leaf)

    roles.assign_role(
        db,
        role=viewer,
        principal_type=PrincipalType.user,
        principal_id=reader.id,
        entry_id=root.id,
    )
    assert authz.can(db, reader, "read", leaf)


def test_verschieben_aendert_vererbung_nach_flush(db):
    owner = make_user(db, "owner-move")
    member = make_user(db, "member-move")
    root = namespace.ensure_root(db, owner.id)
    shared = namespace.create_folder(db, root, "geteilt", owner.id)
    private = namespace.create_folder(db, root, "privat", owner.id)
    sub = namespace.create_folder(db, private, "unterordner", owner.id)
    _grant_read(
        db, shared, PrincipalType.user, member.id, perms=authz.READ | authz.WRITE
    )

    assert not authz.can(db, member, "read", sub)

    namespace.move(db, sub, shared)
    assert authz.can(db, member, "read", sub)
    assert authz.can(db, member, "write", sub)
    assert not authz.can(db, member, "delete", sub)

    # Zurückschieben entzieht die geerbten Rechte ebenfalls sofort.
    namespace.move(db, sub, private)
    assert not authz.can(db, member, "read", sub)
    assert authz.effective_perms(db, member, sub) == 0


# --- Systemadmin und Zyklusschutz -------------------------------------------


def test_system_admin_bleibt_ohne_dateizugriff_trotz_cache(db):
    admin = make_user(db, "sysadmin")
    owner = make_user(db, "owner-admin")
    role = roles.create_role(
        db, name="ops-admin", scope=RoleScope.system, permissions=authz.ADMIN
    )
    roles.assign_role(
        db, role=role, principal_type=PrincipalType.user, principal_id=admin.id
    )
    assert authz.is_system_admin(db, admin)

    _root, leaf = _deep_chain(db, owner, depth=3)
    assert authz.effective_perms(db, admin, leaf) == 0
    assert not authz.can(db, admin, "read", leaf)

    # Zweiter Aufruf kommt aus dem Cache (keine Query).
    assert authz.is_system_admin(db, admin)
    with query_counter() as statements:
        assert authz.is_system_admin(db, admin)
    assert statements == []

    # Systemrolle entziehen -> Adminstatus weg (nach Flush).
    assignment = roles.list_assignments(db, principal_id=admin.id)[0]
    roles.remove_assignment(db, assignment)
    assert not authz.is_system_admin(db, admin)


def test_zyklus_terminiert_weiterhin(db):
    owner = make_user(db, "owner-zyklus")
    other = make_user(db, "other-zyklus")
    root = namespace.ensure_root(db, owner.id)
    a = namespace.create_folder(db, root, "a", owner.id)
    b = namespace.create_folder(db, root, "b", owner.id)
    # Zyklus direkt in der DB erzeugen (wie beim früheren Race).
    db.execute(update(Entry).where(Entry.id == a.id).values(parent_id=b.id))
    db.execute(update(Entry).where(Entry.id == b.id).values(parent_id=a.id))
    db.flush()
    db.expire_all()

    entry = db.get(Entry, a.id)
    assert authz.can(db, other, "read", entry) is False
    assert authz.effective_perms(db, other, entry) == 0
